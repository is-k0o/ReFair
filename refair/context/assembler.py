"""Read-only SQLite assembly for operation-centric context snapshots."""

from __future__ import annotations

from uuid import UUID

from refair.analytics import (
    derive_json_duplicate_key_leads,
    derive_multipart_disposition_ambiguity_leads,
    derive_unobserved_advertised_method_leads,
)
from refair.context.compiler import (
    DEFAULT_CONTEXT_LIMITS,
    ContextLimits,
    OperationContextInput,
    compile_operation_context,
)
from refair.models import (
    ContextHypothesis,
    ContextObservationRef,
    OperationContextSnapshot,
)
from refair.storage import SQLiteRepository


def assemble_operation_context(
    repository: SQLiteRepository,
    operation_id: UUID,
    *,
    limits: ContextLimits = DEFAULT_CONTEXT_LIMITS,
) -> OperationContextSnapshot:
    """Assemble and compile one operation context without reading RAW BLOBs."""

    if not repository.read_only:
        raise ValueError("operation context assembly requires a read-only repository")

    operation = repository.get_http_operation(operation_id)
    if operation is None:
        raise ValueError(f"unknown HTTP operation: {operation_id}")
    endpoint = repository.get_exact_endpoint(operation.endpoint_id)
    if endpoint is None:
        raise RuntimeError(
            f"HTTP operation {operation_id} references a missing exact endpoint"
        )

    project_id = endpoint.project_id
    operations = repository.list_http_operations(endpoint_id=endpoint.id)
    advertisements = repository.list_method_advertisements(endpoint_id=endpoint.id)
    metadata = repository.operation_observation_metadata(operation.id)
    for item in metadata:
        if item.project_id != project_id:
            raise RuntimeError(
                "operation observation project conflicts with anchor endpoint"
            )
    observation_refs = tuple(
        ContextObservationRef(
            observation_id=item.observation_id,
            project_id=item.project_id,
            observed_at=item.observed_at,
            actor_id=item.actor_id,
            provenance=item.provenance,
            response_status=item.response_status,
        )
        for item in metadata
    )

    duplicate_json_fields = repository.operation_json_field_observations(
        operation.id,
        duplicate_keys_only=True,
    )
    multipart_parts = repository.operation_multipart_parts(operation.id)
    exploration_leads = (
        *derive_unobserved_advertised_method_leads(
            project_id=project_id,
            endpoint=endpoint,
            operations=operations,
            advertisements=advertisements,
        ),
        *derive_json_duplicate_key_leads(
            project_id=project_id,
            operation=operation,
            fields=duplicate_json_fields,
        ),
        *derive_multipart_disposition_ambiguity_leads(
            project_id=project_id,
            operation=operation,
            parts=multipart_parts,
        ),
    )

    hypotheses: list[ContextHypothesis] = []
    for item in repository.operation_hypothesis_witnesses(operation.id):
        if item.project_id != project_id:
            raise RuntimeError("hypothesis project conflicts with anchor endpoint")
        hypotheses.append(
            ContextHypothesis(
                id=item.id,
                project_id=item.project_id,
                statement=item.statement,
                status=item.status,
                supporting_witness_observation_ids=item.supporting_observation_ids,
                contradicting_witness_observation_ids=(
                    item.contradicting_observation_ids
                ),
            )
        )

    source = OperationContextInput(
        project_id=project_id,
        endpoint=endpoint,
        operation=operation,
        sibling_operations=operations,
        method_advertisements=advertisements,
        observation_refs=observation_refs,
        query_shapes=repository.operation_query_shapes(operation.id),
        request_representations=repository.operation_request_representations(
            operation.id
        ),
        response_representations=repository.operation_response_representations(
            operation.id
        ),
        actor_outcomes=repository.operation_actor_outcomes(operation.id),
        json_document_outcomes=repository.operation_json_document_outcomes(
            operation.id
        ),
        json_fields=repository.operation_json_fields(operation.id),
        form_document_outcomes=repository.operation_form_document_outcomes(
            operation.id
        ),
        form_fields=repository.operation_form_fields(operation.id),
        multipart_document_outcomes=(
            repository.operation_multipart_document_outcomes(operation.id)
        ),
        multipart_parts=multipart_parts,
        exploration_leads=exploration_leads,
        hypotheses=tuple(hypotheses),
    )
    return compile_operation_context(source, limits)
