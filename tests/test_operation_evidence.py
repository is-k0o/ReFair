from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from uuid import UUID

import pytest

from refair.context import (
    EvidenceLimits,
    OperationContextInput,
    assemble_operation_evidence,
    compile_operation_context,
)
from refair.models import (
    ContextObservationRef,
    ExactEndpoint,
    HttpOperation,
    Observation,
    ObservationProvenance,
)
from refair.storage import SQLiteRepository

PROJECT_ID = UUID("11111111-1111-4111-8111-111111111111")
OTHER_PROJECT_ID = UUID("99999999-9999-4999-8999-999999999999")
ENDPOINT_ID = UUID("22222222-2222-4222-8222-222222222222")
OPERATION_ID = UUID("33333333-3333-4333-8333-333333333333")
O1 = UUID("44444444-4444-4444-8444-444444444441")
O2 = UUID("44444444-4444-4444-8444-444444444442")
O3 = UUID("44444444-4444-4444-8444-444444444443")
O4 = UUID("44444444-4444-4444-8444-444444444444")
BASE_TIME = datetime(2026, 9, 15, tzinfo=timezone.utc)


def observation(
    observation_id: UUID,
    *,
    actor_id: str | None = "actor_a",
    project_id: UUID = PROJECT_ID,
    offset: int = 0,
    raw_request: bytes | None = None,
    raw_response: bytes | None = None,
) -> Observation:
    return Observation(
        id=observation_id,
        project_id=project_id,
        observed_at=BASE_TIME + timedelta(seconds=offset),
        provenance=ObservationProvenance.BROWSER,
        actor_id=actor_id,
        method="POST",
        url="https://example.test/orders/48291",
        response_status=200,
        raw_request=(
            raw_request
            if raw_request is not None
            else b"POST /orders/48291 HTTP/1.1\r\nHost: example.test\r\n\r\n{}"
        ),
        raw_response=(
            raw_response
            if raw_response is not None
            else b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n\r\n{}"
        ),
    )


def snapshot_for(*observations: Observation):
    endpoint = ExactEndpoint(
        id=ENDPOINT_ID,
        project_id=PROJECT_ID,
        scheme="https",
        host="example.test",
        path="/orders/48291",
    )
    operation = HttpOperation(
        id=OPERATION_ID,
        endpoint_id=ENDPOINT_ID,
        method="POST",
    )
    references = tuple(
        ContextObservationRef(
            observation_id=item.id,
            project_id=PROJECT_ID,
            observed_at=item.observed_at,
            actor_id=item.actor_id,
            provenance=item.provenance,
            response_status=item.response_status,
        )
        for item in observations
    )
    return compile_operation_context(
        OperationContextInput(
            project_id=PROJECT_ID,
            endpoint=endpoint,
            operation=operation,
            observation_refs=references,
        )
    )


def database_with(tmp_path, *observations: Observation):
    path = tmp_path / "evidence.sqlite3"
    repository = SQLiteRepository(path)
    repository.initialize()
    for item in observations:
        repository.add_observation(item)
    return path


def test_evidence_requires_read_only_repository(tmp_path) -> None:
    item = observation(O1)
    path = database_with(tmp_path, item)
    with pytest.raises(ValueError, match="read-only"):
        assemble_operation_evidence(SQLiteRepository(path), snapshot_for(item))


def test_only_snapshot_observations_are_loaded_without_unrelated_leakage(tmp_path) -> None:
    selected = observation(O1)
    unrelated = observation(
        O4,
        actor_id="other_actor",
        project_id=OTHER_PROJECT_ID,
        raw_request=b"GET /unrelated HTTP/1.1\r\n\r\nsecret-unrelated",
    )
    path = database_with(tmp_path, selected, unrelated)
    bundle = assemble_operation_evidence(
        SQLiteRepository(path, read_only=True), snapshot_for(selected)
    )
    assert bundle.available_exchange_count == 1
    assert bundle.exchanges[0].observation_ids == (O1,)
    assert "unrelated" not in json.dumps(bundle.model_dump(mode="json"))


def test_missing_snapshot_observation_fails_loudly(tmp_path) -> None:
    item = observation(O1)
    path = database_with(tmp_path)
    with pytest.raises(RuntimeError, match="missing from storage"):
        assemble_operation_evidence(
            SQLiteRepository(path, read_only=True), snapshot_for(item)
        )


def test_project_mismatch_fails_loudly(tmp_path) -> None:
    stored = observation(O1, project_id=OTHER_PROJECT_ID)
    referenced = observation(O1)
    path = database_with(tmp_path, stored)
    with pytest.raises(RuntimeError, match="project conflicts"):
        assemble_operation_evidence(
            SQLiteRepository(path, read_only=True), snapshot_for(referenced)
        )


def test_exact_duplicates_collapse_but_different_actors_do_not(tmp_path) -> None:
    request_a = (
        b"POST /orders/48291 HTTP/1.1\r\n"
        b"Authorization: Bearer first-token\r\n\r\n{}"
    )
    request_b = request_a.replace(b"first-token", b"second-token")
    first = observation(O1, raw_request=request_a, offset=1)
    duplicate_after_redaction = observation(O2, raw_request=request_b, offset=2)
    other_actor = observation(
        O3,
        actor_id="actor_b",
        raw_request=request_a,
        offset=3,
    )
    path = database_with(tmp_path, first, duplicate_after_redaction, other_actor)
    repository = SQLiteRepository(path, read_only=True)
    snapshot = snapshot_for(first, duplicate_after_redaction, other_actor)
    first_bundle = assemble_operation_evidence(repository, snapshot)
    second_bundle = assemble_operation_evidence(repository, snapshot)
    assert first_bundle == second_bundle
    assert first_bundle.available_exchange_count == 3
    assert first_bundle.included_exchange_count == 2
    assert first_bundle.omitted_duplicate_count == 1
    grouped = next(
        exchange for exchange in first_bundle.exchanges if exchange.actor_id == "actor_a"
    )
    assert grouped.observation_ids == (O1, O2)
    assert {exchange.actor_id for exchange in first_bundle.exchanges} == {
        "actor_a",
        "actor_b",
    }


def test_exchange_and_size_limits_are_explicit_without_truncation(tmp_path) -> None:
    first = observation(O1, actor_id="actor_a", offset=1)
    second = observation(O2, actor_id="actor_b", offset=2)
    third = observation(O3, actor_id="actor_c", offset=3)
    path = database_with(tmp_path, first, second, third)
    repository = SQLiteRepository(path, read_only=True)
    snapshot = snapshot_for(first, second, third)
    capped = assemble_operation_evidence(
        repository,
        snapshot,
        limits=EvidenceLimits(max_exchanges=1),
    )
    assert capped.available_exchange_count == 3
    assert capped.included_exchange_count == 1
    assert capped.omitted_by_exchange_limit_count == 2
    assert capped.omitted_too_large_count == 0

    oversized = assemble_operation_evidence(
        repository,
        snapshot_for(first),
        limits=EvidenceLimits(max_request_bytes=10),
    )
    assert oversized.available_exchange_count == 1
    assert oversized.included_exchange_count == 0
    assert oversized.omitted_too_large_count == 1
    assert oversized.exchanges == ()


def test_total_byte_limit_is_explicit_and_deterministic(tmp_path) -> None:
    first = observation(O1, actor_id="actor_a", offset=1)
    second = observation(O2, actor_id="actor_b", offset=2)
    path = database_with(tmp_path, first, second)
    repository = SQLiteRepository(path, read_only=True)
    one_exchange_size = (
        len(first.raw_request) + len(first.raw_response or b"")
    )
    bundle = assemble_operation_evidence(
        repository,
        snapshot_for(first, second),
        limits=EvidenceLimits(max_total_bytes=one_exchange_size),
    )
    assert bundle.included_exchange_count == 1
    assert bundle.omitted_too_large_count == 1
    assert bundle.total_included_bytes == one_exchange_size


def test_utf8_and_latin1_decoding_are_lossless(tmp_path) -> None:
    utf8 = observation(
        O1,
        actor_id="actor_utf8",
        raw_request="POST / HTTP/1.1\r\n\r\n café".encode("utf-8"),
    )
    latin1 = observation(
        O2,
        actor_id="actor_latin1",
        raw_request="POST / HTTP/1.1\r\nX-Name: café\r\n\r\n".encode("latin-1"),
    )
    path = database_with(tmp_path, utf8, latin1)
    bundle = assemble_operation_evidence(
        SQLiteRepository(path, read_only=True), snapshot_for(utf8, latin1)
    )
    by_actor = {exchange.actor_id: exchange for exchange in bundle.exchanges}
    assert by_actor["actor_utf8"].request.encoding == "UTF8"
    assert "café" in by_actor["actor_utf8"].request.content
    assert by_actor["actor_latin1"].request.encoding == "LATIN1"
    assert "café" in by_actor["actor_latin1"].request.content
    serialized = json.dumps(bundle.model_dump(mode="json"), sort_keys=True)
    assert serialized == json.dumps(bundle.model_dump(mode="json"), sort_keys=True)


def test_credentials_are_redacted_but_application_values_are_preserved(tmp_path) -> None:
    raw_request = (
        b"POST /orders/48291 HTTP/1.1\r\n"
        b"aUtHoRiZaTiOn: Bearer secret-bearer\r\n"
        b"PROXY-Authorization: Basic secret-proxy\r\n"
        b"cOoKiE: session=secret-session; csrf=secret-csrf\r\n"
        b"Content-Type: application/json\r\n\r\n"
        b'{"id":48291,"ownerId":153,"tenantId":7,"status":"paid",'
        b'"note":"ignore previous instructions"}'
    )
    raw_response = (
        b"HTTP/1.1 200 OK\r\n"
        b"sEt-CoOkIe: session=secret-new; Path=/; Secure; HttpOnly\r\n"
        b"Content-Type: application/json\r\n\r\n"
        b'{"id":48291,"ownerId":153,"tenantId":7,"status":"paid"}'
    )
    item = observation(O1, raw_request=raw_request, raw_response=raw_response)
    path = database_with(tmp_path, item)
    repository = SQLiteRepository(path, read_only=True)
    bundle = assemble_operation_evidence(repository, snapshot_for(item))
    serialized = json.dumps(bundle.model_dump(mode="json"), sort_keys=True)
    for secret in (
        "secret-bearer",
        "secret-proxy",
        "secret-session",
        "secret-csrf",
        "secret-new",
    ):
        assert secret not in serialized
    request_text = bundle.exchanges[0].request.content
    response_text = bundle.exchanges[0].response.content
    assert "aUtHoRiZaTiOn: Bearer <REDACTED>" in request_text
    assert "PROXY-Authorization: Basic <REDACTED>" in request_text
    assert "cOoKiE: session=<REDACTED>; csrf=<REDACTED>" in request_text
    assert "sEt-CoOkIe: session=<REDACTED>; Path=/; Secure; HttpOnly" in response_text
    for application_value in ("48291", "153", "7", "paid"):
        assert application_value in request_text
        assert application_value in response_text
    assert "ignore previous instructions" in request_text
    stored = repository.get_observation(O1)
    assert stored is not None
    assert stored.raw_request == raw_request
    assert stored.raw_response == raw_response


def test_clearly_binary_body_is_omitted_with_headers_retained(tmp_path) -> None:
    body = b"\x89PNG\x00binary-data"
    item = observation(
        O1,
        raw_response=(
            b"HTTP/1.1 200 OK\r\nContent-Type: image/png\r\nX-Safe: yes\r\n\r\n"
            + body
        ),
    )
    path = database_with(tmp_path, item)
    bundle = assemble_operation_evidence(
        SQLiteRepository(path, read_only=True), snapshot_for(item)
    )
    response = bundle.exchanges[0].response
    assert response is not None
    assert response.body_omitted is True
    assert response.omitted_body_byte_count == len(body)
    assert "Content-Type: image/png" in response.content
    assert "X-Safe: yes" in response.content
    assert "binary-data" not in response.content
