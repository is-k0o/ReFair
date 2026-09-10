from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from uuid import UUID

import pytest

from refair.context import (
    DEFAULT_CONTEXT_LIMITS,
    ContextLimits,
    assemble_operation_context,
)
from refair.models import (
    ContextSection,
    Hypothesis,
    JsonDirection,
    MultipartDirection,
    Observation,
    ObservationProvenance,
)
from refair.normalization import normalize_observation
from refair.storage import SQLiteRepository
from refair.structure import extract_structure

PROJECT_ID = UUID("11111111-1111-4111-8111-111111111111")
O1 = UUID("20000000-0000-4000-8000-000000000001")
O2 = UUID("20000000-0000-4000-8000-000000000002")
O3 = UUID("20000000-0000-4000-8000-000000000003")
O4 = UUID("20000000-0000-4000-8000-000000000004")
O5 = UUID("20000000-0000-4000-8000-000000000005")
O6 = UUID("20000000-0000-4000-8000-000000000006")
BASE_TIME = datetime(2026, 9, 10, 10, tzinfo=timezone.utc)
SECRET_MARKER = "raw-secret-marker-c3b"


def limits(**updates) -> ContextLimits:
    values = DEFAULT_CONTEXT_LIMITS.model_dump()
    values.update(updates)
    return ContextLimits(**values)


def observation(
    *,
    id: UUID,
    method: str,
    url: str,
    request_content_type: str | None = None,
    request_body: bytes = b"",
    response_headers: bytes = b"",
    response_content_type: str | None = "application/json",
    response_body: bytes | None = None,
    actor_id: str = "actor_a",
    offset: int = 0,
) -> Observation:
    request_headers = (
        f"Content-Type: {request_content_type}\r\n".encode("ascii")
        if request_content_type is not None
        else b""
    )
    target = url.split(".test", 1)[1]
    return Observation(
        id=id,
        project_id=PROJECT_ID,
        observed_at=BASE_TIME + timedelta(seconds=offset),
        provenance=ObservationProvenance.BROWSER,
        actor_id=actor_id,
        method=method,
        url=url,
        response_status=200,
        raw_request=(
            f"{method} {target} HTTP/1.1\r\n".encode("ascii")
            + request_headers
            + b"\r\n"
            + request_body
        ),
        raw_response=(
            b"HTTP/1.1 200 OK\r\n"
            + response_headers
            + (
                f"Content-Type: {response_content_type}\r\n".encode("ascii")
                if response_content_type is not None
                else b""
            )
            + b"\r\n"
            + (
                response_body
                if response_body is not None
                else b'{"response":"'
                + SECRET_MARKER.encode("ascii")
                + b'"}'
            )
        ),
    )


def multipart_body(*, ambiguous: bool) -> bytes:
    first = (
        b'Content-Disposition: form-data; name="a"; name="b"'
        if ambiguous
        else b'Content-Disposition: form-data; name="single"'
    )
    return (
        b"--b\r\n"
        + first
        + b"\r\n\r\n"
        + SECRET_MARKER.encode("ascii")
        + b"\r\n--b\r\nContent-Disposition: form-data; name=\"tag\""
        b"\r\n\r\none\r\n--b\r\nContent-Disposition: form-data; name=\"tag\""
        b"\r\n\r\ntwo\r\n--b--\r\n"
    )


def persist(repository: SQLiteRepository, item: Observation):
    repository.add_observation(item)
    normalized = repository.add_normalized_exchange(normalize_observation(item))
    extraction = extract_structure(item, normalized)
    assert repository.add_structural_extraction(extraction)
    return extraction


def build_database(tmp_path):
    database = tmp_path / "context.sqlite3"
    repository = SQLiteRepository(database)
    repository.initialize()
    anchor_json = persist(
        repository,
        observation(
            id=O1,
            method="GET",
            url="https://api.test/users?id=one&view=full",
            request_content_type="application/json",
            request_body=(
                b'{"id":1,"id":"' + SECRET_MARKER.encode("ascii") + b'","other":true}'
            ),
            response_headers=b"Allow: GET, POST\r\n",
            offset=1,
        ),
    )
    persist(
        repository,
        observation(
            id=O2,
            method="GET",
            url="https://api.test/users?id=two",
            request_content_type="multipart/form-data; boundary=b",
            request_body=multipart_body(ambiguous=True),
            actor_id="actor_b",
            offset=2,
        ),
    )
    persist(
        repository,
        observation(
            id=O5,
            method="GET",
            url="https://api.test/users",
            request_content_type="application/x-www-form-urlencoded",
            request_body=b"field=" + SECRET_MARKER.encode("ascii"),
            offset=5,
        ),
    )
    other_json = persist(
        repository,
        observation(
            id=O4,
            method="GET",
            url="https://api.test/other",
            request_content_type="application/json",
            request_body=b'{"other":1,"other":2}',
            response_headers=b"Allow: GET, DELETE\r\n",
            offset=4,
        ),
    )
    persist(
        repository,
        observation(
            id=O6,
            method="GET",
            url="https://api.test/other",
            request_content_type="multipart/form-data; boundary=b",
            request_body=multipart_body(ambiguous=True),
            offset=6,
        ),
    )
    assert anchor_json.operation.id != other_json.operation.id
    return database, anchor_json.operation.id, anchor_json.endpoint.id, other_json.operation.id


def add_sibling(repository: SQLiteRepository):
    return persist(
        repository,
        observation(
            id=O3,
            method="POST",
            url="https://api.test/users",
            request_content_type="application/x-www-form-urlencoded",
            request_body=b"posted=" + SECRET_MARKER.encode("ascii"),
            offset=3,
        ),
    )


def test_anchor_lookups_and_unknown_results_are_exact(tmp_path) -> None:
    database, operation_id, endpoint_id, _ = build_database(tmp_path)
    repository = SQLiteRepository(database, read_only=True)
    operation = repository.get_http_operation(operation_id)
    endpoint = repository.get_exact_endpoint(endpoint_id)
    assert operation is not None and operation.id == operation_id
    assert endpoint is not None and endpoint.id == endpoint_id
    assert endpoint.project_id == PROJECT_ID
    unknown = UUID("ffffffff-ffff-4fff-8fff-ffffffffffff")
    assert repository.get_http_operation(unknown) is None
    assert repository.get_exact_endpoint(unknown) is None
    with pytest.raises(ValueError, match="unknown HTTP operation"):
        assemble_operation_context(repository, unknown)


def test_same_endpoint_expansion_and_metadata_are_operation_scoped(tmp_path) -> None:
    database, operation_id, endpoint_id, other_operation_id = build_database(tmp_path)
    writable = SQLiteRepository(database)
    sibling = add_sibling(writable)
    repository = SQLiteRepository(database, read_only=True)
    snapshot = assemble_operation_context(repository, operation_id)

    assert snapshot.endpoint.id == endpoint_id
    assert snapshot.project_id == PROJECT_ID
    assert snapshot.sibling_operations == (sibling.operation,)
    assert all(item.endpoint_id == endpoint_id for item in snapshot.method_advertisements)
    assert {item.observation_id for item in snapshot.observation_refs} == {O1, O2, O5}
    assert O3 not in {item.observation_id for item in snapshot.observation_refs}
    metadata = repository.operation_observation_metadata(operation_id)
    assert tuple(item.observation_id for item in metadata) == (O5, O2, O1)
    assert metadata == repository.operation_observation_metadata(operation_id)
    assert repository.operation_observation_metadata(other_operation_id)
    assert repository.operation_observation_metadata(UUID(int=0)) == ()
    assert set(type(metadata[0]).__annotations__) == {
        "observation_id",
        "project_id",
        "observed_at",
        "provenance",
        "actor_id",
        "response_status",
    }
    dumped = json.dumps(snapshot.model_dump(mode="json"))
    assert SECRET_MARKER not in dumped
    assert "raw_request" not in dumped and "raw_response" not in dumped


def test_existing_aggregate_readers_remain_anchor_operation_scoped(tmp_path) -> None:
    database, operation_id, _, _ = build_database(tmp_path)
    snapshot = assemble_operation_context(
        SQLiteRepository(database, read_only=True), operation_id
    )
    assert snapshot.query_shapes
    assert snapshot.request_representations
    assert snapshot.response_representations
    assert {item.actor_id for item in snapshot.actor_outcomes} == {"actor_a", "actor_b"}
    assert snapshot.json_document_outcomes and snapshot.json_fields
    assert snapshot.form_document_outcomes and snapshot.form_fields
    assert snapshot.multipart_document_outcomes and snapshot.multipart_parts
    assert all(item.observation_id == O2 for item in snapshot.multipart_parts)


def test_three_c2_rule_families_integrate_without_cross_operation_leakage(
    tmp_path,
) -> None:
    database, operation_id, _, _ = build_database(tmp_path)
    repository = SQLiteRepository(database, read_only=True)
    before = assemble_operation_context(repository, operation_id)
    assert len(before.exploration_leads) == 3
    assert any("advertised method" in lead.question for lead in before.exploration_leads)
    assert any("duplicate JSON" in lead.question for lead in before.exploration_leads)
    multipart_leads = [
        lead
        for lead in before.exploration_leads
        if "duplicate multipart" in lead.question
    ]
    assert len(multipart_leads) == 1
    assert {item.part_index for item in before.multipart_parts} == {0, 1, 2}

    duplicate_fields = repository.operation_json_field_observations(
        operation_id, duplicate_keys_only=True
    )
    assert duplicate_fields
    assert duplicate_fields == repository.operation_json_field_observations(
        operation_id, duplicate_keys_only=True
    )
    assert duplicate_fields == tuple(
        sorted(
            duplicate_fields,
            key=lambda item: (
                str(item.observation_id),
                item.direction.value,
                repr(item.path),
                item.json_type.value,
            ),
        )
    )
    assert all(item.observation_id == O1 for item in duplicate_fields)
    assert all(item.duplicate_key_observed for item in duplicate_fields)
    all_fields = repository.operation_json_field_observations(operation_id)
    assert len(all_fields) > len(duplicate_fields)
    assert repository.operation_json_field_observations(UUID(int=0)) == ()

    add_sibling(SQLiteRepository(database))
    after = assemble_operation_context(
        SQLiteRepository(database, read_only=True), operation_id
    )
    assert len(after.exploration_leads) == 2
    assert not any("advertised method" in lead.question for lead in after.exploration_leads)


def test_hypotheses_use_only_sorted_operation_local_evidence_witnesses(tmp_path) -> None:
    database, operation_id, _, _ = build_database(tmp_path)
    writable = SQLiteRepository(database)
    add_sibling(writable)
    related = Hypothesis(
        id=UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"),
        project_id=PROJECT_ID,
        statement="Related hypothesis",
        supporting_evidence_ids=(O5, O3, O1),
        contradicting_evidence_ids=(O2,),
    )
    unrelated = Hypothesis(
        id=UUID("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"),
        project_id=PROJECT_ID,
        statement="Sibling-only hypothesis",
        supporting_evidence_ids=(O3,),
    )
    writable.add_hypothesis(related)
    writable.add_hypothesis(unrelated)

    repository = SQLiteRepository(database, read_only=True)
    projections = repository.operation_hypothesis_witnesses(operation_id)
    assert len(projections) == 1
    assert projections[0].supporting_observation_ids == (O1, O5)
    assert projections[0].contradicting_observation_ids == (O2,)
    assert repository.operation_hypothesis_witnesses(UUID(int=0)) == ()
    snapshot = assemble_operation_context(repository, operation_id)
    assert len(snapshot.hypotheses) == 1
    assert snapshot.hypotheses[0].id == related.id
    assert O3 not in snapshot.hypotheses[0].supporting_witness_observation_ids


def test_hypothesis_projection_order_is_deterministic(tmp_path) -> None:
    database, operation_id, _, _ = build_database(tmp_path)
    writable = SQLiteRepository(database)
    later = Hypothesis(
        id=UUID("dddddddd-dddd-4ddd-8ddd-dddddddddddd"),
        project_id=PROJECT_ID,
        statement="Later hypothesis",
        supporting_evidence_ids=(O1,),
    )
    earlier = Hypothesis(
        id=UUID("11111111-2222-4333-8444-555555555555"),
        project_id=PROJECT_ID,
        statement="Earlier hypothesis",
        contradicting_evidence_ids=(O2,),
    )
    writable.add_hypothesis(later)
    writable.add_hypothesis(earlier)

    repository = SQLiteRepository(database, read_only=True)
    first = repository.operation_hypothesis_witnesses(operation_id)
    second = repository.operation_hypothesis_witnesses(operation_id)
    assert first == second
    assert tuple(item.id for item in first) == (earlier.id, later.id)


def integrity(database):
    with sqlite3.connect(database) as connection:
        return (
            connection.execute("PRAGMA user_version").fetchone()[0],
            connection.execute("SELECT COUNT(*) FROM observations").fetchone()[0],
            connection.execute("SELECT COUNT(*) FROM exact_endpoints").fetchone()[0],
            connection.execute("SELECT COUNT(*) FROM http_operations").fetchone()[0],
            connection.execute(
                "SELECT SUM(LENGTH(raw_request)), SUM(LENGTH(raw_response)) FROM observations"
            ).fetchone(),
        )


def test_assembly_requires_read_only_and_changes_no_database_state(tmp_path) -> None:
    database, operation_id, _, _ = build_database(tmp_path)
    before = integrity(database)
    with pytest.raises(ValueError, match="read-only"):
        assemble_operation_context(SQLiteRepository(database), operation_id)
    repository = SQLiteRepository(database, read_only=True)
    first = assemble_operation_context(repository, operation_id)
    second = assemble_operation_context(repository, operation_id)
    assert first == second
    assert json.dumps(first.model_dump(mode="json")) == json.dumps(
        second.model_dump(mode="json")
    )
    assert integrity(database) == before


def test_c3a_receives_complete_operation_scope_before_applying_limits(tmp_path) -> None:
    database, operation_id, _, _ = build_database(tmp_path)
    snapshot = assemble_operation_context(
        SQLiteRepository(database, read_only=True),
        operation_id,
        limits=limits(max_json_fields=1, max_observation_refs=1),
    )
    json_coverage = next(
        item for item in snapshot.coverage if item.section is ContextSection.JSON_FIELDS
    )
    observation_coverage = next(
        item
        for item in snapshot.coverage
        if item.section is ContextSection.OBSERVATION_REFS
    )
    assert json_coverage.available_count > json_coverage.included_count == 1
    assert json_coverage.omitted_by_limit_count > 0
    assert observation_coverage.available_count == 3
    assert observation_coverage.included_count == 1
    assert observation_coverage.omitted_by_limit_count == 2


def test_sparse_operation_compiles_without_analytic_or_body_state(tmp_path) -> None:
    database = tmp_path / "sparse.sqlite3"
    writable = SQLiteRepository(database)
    writable.initialize()
    extraction = persist(
        writable,
        observation(
                id=O1,
                method="HEAD",
                url="https://api.test/sparse",
                response_content_type=None,
                response_body=b"",
                offset=1,
        ),
    )
    snapshot = assemble_operation_context(
        SQLiteRepository(database, read_only=True), extraction.operation.id
    )
    assert snapshot.exploration_leads == ()
    assert snapshot.json_fields == ()
    assert snapshot.form_fields == ()
    assert snapshot.multipart_parts == ()
    assert snapshot.hypotheses == ()


def test_missing_endpoint_is_reported_as_inconsistent_state(tmp_path) -> None:
    database, operation_id, endpoint_id, _ = build_database(tmp_path)
    with sqlite3.connect(database) as connection:
        connection.execute("PRAGMA foreign_keys = OFF")
        connection.execute("DELETE FROM exact_endpoints WHERE id = ?", (str(endpoint_id),))
    with pytest.raises(RuntimeError, match="missing exact endpoint"):
        assemble_operation_context(
            SQLiteRepository(database, read_only=True), operation_id
        )
