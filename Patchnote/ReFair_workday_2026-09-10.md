# ReFair — Workday patchnote — 2026-09-10

## Summary

Today moved ReFair from passive structural modeling into the first complete operation-scoped reasoning context pipeline.

The main progression was:

```text
L2 STRUCTURAL
    ↓
L3 ANALYTIC LEADS
    ↓
BOUNDED OPERATION CONTEXT
    ↓
READ-ONLY SQLITE ASSEMBLY
```

Five repository milestones were completed and pushed:

- `e6b9b29aeb74b7e232e11edf2308a57825d26196` — `Implemented V0.2-C1`
- `77d189bbac9f52afcb3fafdf82ceb9a62daf13bd` — `Clean up local multipart smoke artifacts`
- `d7305c367462af51bbf1322e15fb52fc72333ec3` — `Implemented V0.2-C2`
- `06729fd9caeadcc266f05596c1983d47dfe2e3a7` — `Implemented V0.2-C3a`
- `d73022f8fe02e6d73cd13bf9345bfe684e1e6834` — `Implemented V0.2-C3b`

At the end of the day:

- current pushed `main`: `d73022f8fe02e6d73cd13bf9345bfe684e1e6834`
- public package version remains `0.1.5`
- `NORMALIZER_VERSION = 2`
- SQLite schema version remains `6`
- `STRUCTURAL_VERSION = 5`
- full Python suite reported by the C3b implementation: **258 passed**
- focused C3b test rerun: **10 passed in 4.78s**
- real `refair.sqlite3` operation-context smoke: **passed**
- no Astra / LLM provider was connected
- no active HTTP execution was added
- no policy engine or executor was added
- no schema migration was required

The key architectural milestone is that ReFair can now take one real persisted `HttpOperation` and deterministically assemble a bounded, JSON-serializable context containing only operation-related structural evidence, C2 analytic leads, and operation-local hypothesis witnesses.

This is the first point where the previously separate layers actually meet on real captured traffic.

---

# 1. V0.2-C1 — Analytic + planner contracts

C1 established the minimum contracts required between deterministic structural knowledge, analytic interpretation, and future planning.

The design deliberately avoided implementing Astra, persistence, execution, or a generic analytic framework.

## Core separation

The intended flow is now:

```text
L2 STRUCTURAL
    what ReFair observed

        ↓

L3 ANALYTIC
    what those observations may suggest

        ↓

PLANNER
    what should be considered next
```

A planner decision is not application knowledge.

For example:

```text
Hypothesis
    "Actor B may be able to access Actor A's invoice."

ExperimentProposal
    "Replay a known operation as actor B with a controlled object change."
```

The first is an interpretation of evidence.

The second is a proposed next action.

Keeping those objects distinct is important for later calibration and policy enforcement.

## ExplorationLead

C1 introduced `ExplorationLead` as the minimal L3 object for evidence-backed uncertainty that does not yet justify a vulnerability hypothesis.

This solved a recurring design problem: ReFair must be able to surface a precise structural lead without manufacturing a fake vulnerability hypothesis.

An `ExplorationLead` requires at least one immutable evidence anchor:

- `ObservationGrounding`
- or `AssetGrounding`

Structural references may also be present:

- `ExactEndpointGrounding`
- `HttpOperationGrounding`

The stronger invariant retained for later active work is:

> No active action without precise informational justification.

This replaces the overly restrictive idea that every action must already have a vulnerability hypothesis.

## Planner decision contracts

C1 introduced exactly three planner output types:

- `ExperimentProposal`
- `ExplorationProposal`
- `WaitDecision`

with a discriminated union:

```text
PlannerDecision
```

`ExperimentProposal` represents a proposed experiment against an existing hypothesis. It carries the project, hypothesis, actor, baseline observation, typed target, rationale, intended change, expected secure outcome, expected interesting outcome, and evidence grounding. It does not authorize execution.

`ExplorationProposal` represents one bounded information-gathering action for an `ExplorationLead`. Its requested request cap is only a planner request; the future policy engine remains authoritative for scope, budgets, rate, concurrency, mutation, danger classification, and approval.

`WaitDecision` allows the planner to explicitly conclude that no justified action should be taken yet. ReFair must be able to stop rather than converting uncertainty into automatic fuzzing.

## Typed planner targets

C1 introduced typed targets rather than arbitrary URL strings:

- `ExistingOperationTarget`
- `ExactEndpointTarget`
- `AssetReferencedUrlTarget`

No generic HTTP request DSL was added.

No `FUZZ`, `SCAN`, or `ENUMERATE` planner decision was introduced.

## Validation

C1 finished with:

**198 tests passed**

Commit:

`e6b9b29aeb74b7e232e11edf2308a57825d26196` — `Implemented V0.2-C1`

C1 was then frozen.

---

# 2. Cleanup after C1

The C1 commit accidentally included two local multipart smoke artifacts:

- `multipart-smoke.html`
- `refair-smoke.txt`

Those files were development artifacts, not project state.

They were removed from Git and ignored explicitly.

Commit:

`77d189bbac9f52afcb3fafdf82ceb9a62daf13bd` — `Clean up local multipart smoke artifacts`

No production behavior changed.

This cleanup was intentionally done before C2 so local smoke files could not quietly become dependencies.

---

# 3. V0.2-C2 — Deterministic analytic leads

C2 implemented the first deterministic transformation from L2 structural facts into L3 `ExplorationLead` objects.

The scope was intentionally very small.

C2 does not scan the whole database, choose application scope, infer actor coverage, parse JavaScript, mine assets, discover routes, generate hypotheses, generate planner decisions, execute HTTP, or assign confidence/severity.

It receives explicit caller-supplied structural facts and derives only three high-signal lead families.

## Public API

Added:

```text
derive_unobserved_advertised_method_leads
derive_json_duplicate_key_leads
derive_multipart_disposition_ambiguity_leads
```

Production files:

```text
refair/analytics/__init__.py
refair/analytics/leads.py
```

## Rule A — advertised HTTP method not observed

This rule compares methods explicitly advertised by the application with operations actually observed on the exact same endpoint.

Example:

```text
Observed:
    GET /resource

Advertised:
    GET
    POST
```

Result:

```text
one ExplorationLead for POST
```

Important semantics:

- method identity remains exact and case-sensitive
- duplicate advertisements collapse
- advertisements from `Allow` and CORS can contribute witnesses
- at most one witness observation per advertisement source is retained
- identical witnesses are deduplicated
- no method is guessed
- no route is inferred

Stable UUIDv5 identity is based on:

```text
advertised-method-unobserved:v1
project
endpoint
exact method
```

## Rule B — duplicate JSON object key observed

This rule creates one lead for each unique:

```text
observation
direction
typed JSON path
```

where a duplicate JSON object key was actually observed.

Multiple JSON types at that same duplicate path still produce one lead.

Canonical path identity distinguishes `("a.b",)` from `("a", "b")` and distinguishes actual JSON keys from array wildcards.

No JSON scalar values are accessed.

Stable UUIDv5 identity is based on:

```text
json-duplicate-key:v1
project
operation
observation
direction
canonical typed path
```

## Rule C — duplicate multipart Content-Disposition parameters

This rule fires only when one actual multipart part contains:

```text
name_parameter_count > 1
```

or:

```text
filename_parameter_count > 1
```

Both conditions in the same part produce one lead.

A crucial regression case was preserved:

```text
part 1: name="tag"
part 2: name="tag"
```

with one `name=` parameter per part is normal repeated multipart behavior and produces zero ambiguity leads.

No filename value, part value, or multipart body is read.

Stable UUIDv5 identity is based on:

```text
multipart-ambiguous-disposition:v1
project
operation
observation
direction
part index
```

## Determinism

All C2 lead IDs use UUIDv5 with a fixed private namespace.

No generated lead falls back to `uuid4`.

Equivalent input sets produce the same lead count, IDs, grounding, rationale, and ordering.

Input permutation and repeated-run determinism were tested.

## Deliberately deferred analytics

C2 intentionally did not add:

- actor A seen / actor B unseen leads
- repeated URL-encoded field leads
- generic parse-failure leads
- JavaScript endpoint extraction
- source-map mining
- OpenAPI extraction
- asset URL discovery
- route similarity
- semantic relevance scoring

The actor-coverage rule was specifically deferred because passive browser capture includes unrelated traffic.

Without a bounded application context, a global actor comparison could generate noise from unrelated browser telemetry.

## Validation

Baseline:

**198 passed**

Final:

**226 passed**

Commit:

`d7305c367462af51bbf1322e15fb52fc72333ec3` — `Implemented V0.2-C2`

C2 was then frozen.

---

# 4. C3 design decision — operation-centric bounded context

Before C3 implementation, the central question was:

```text
What exact application state should a future reasoning model receive?
```

The operation-centric model was selected over a project-wide bounded context.

## Anchor

C3 v1 is anchored on exactly:

- one `ExactEndpoint`
- one `HttpOperation` on that endpoint

Allowed implicit structural expansion:

```text
same exact endpoint
    -> sibling HTTP methods
    -> method advertisements
```

No other endpoint relationship is inferred.

C3 v1 does not automatically include same-host endpoints, path-prefix neighbors, semantically similar routes, project-wide operations, asset-derived routes, or graph expansion.

If a relationship is not represented in current state, C3 does not invent it.

## Snapshot is not truth

A central invariant was frozen:

> Snapshot != complete application truth.

A snapshot is:

> a bounded, explicit, reproducible view of caller-supplied known facts.

This led to another important rule:

> Context omission != negative evidence.

If C3 receives 634 unique JSON fields and includes 128, a future model must see that 506 were omitted.

It must not interpret the 128 included fields as the complete application schema.

---

# 5. V0.2-C3a — bounded context contracts and compiler

C3a implemented the context contract and pure deterministic compiler.

It deliberately contains no SQLite logic.

Flow:

```text
caller-supplied facts
        ↓
deduplicate
        ↓
stable sort
        ↓
context projection
        ↓
per-item size checks
        ↓
collection caps
        ↓
coverage accounting
        ↓
OperationContextSnapshot
```

## Snapshot metadata

Every v1 snapshot explicitly states:

```text
snapshot_version = 1
scope = ANCHOR_OPERATION_SAME_ENDPOINT_V1
raw_messages_included = false
credential_material_included = false
scalar_application_values_included = false
```

These fields describe C3 v1. They are not permanent global prohibitions on future evidence enrichment.

## Covered sections

The snapshot contains explicit coverage accounting for exactly 15 sections:

```text
SIBLING_OPERATIONS
METHOD_ADVERTISEMENTS
OBSERVATION_REFS
QUERY_SHAPES
REQUEST_REPRESENTATIONS
RESPONSE_REPRESENTATIONS
ACTOR_OUTCOMES
JSON_DOCUMENT_OUTCOMES
JSON_FIELDS
FORM_DOCUMENT_OUTCOMES
FORM_FIELDS
MULTIPART_DOCUMENT_OUTCOMES
MULTIPART_PARTS
EXPLORATION_LEADS
HYPOTHESES
```

Coverage is present even for empty sections.

Each section records:

```text
available_count
included_count
omitted_too_large_count
omitted_by_limit_count
```

with the invariant:

```text
available
=
included
+ omitted_too_large
+ omitted_by_limit
```

`available_count` means unique facts supplied to C3a after logical deduplication. It does not claim database-wide or project-wide completeness.

## Default collection limits

C3a v1 defaults:

```text
sibling operations           16
method advertisements        32
observation refs             16
query shapes                 32
request representations      16
response representations     32
actor outcomes               32
JSON document outcomes       16
JSON fields                 128
FORM document outcomes       16
FORM fields                 128
multipart document outcomes  16
multipart parts              64
exploration leads            32
hypotheses                   32
```

## Byte-size bounds

Collection limits are not enough because one structural fact could itself be pathological.

C3a therefore also enforces:

```text
anchor serialized bytes = 16,384
item serialized bytes   = 4,096
```

Optional items larger than the per-item cap are omitted whole.

Strings are never silently truncated.

Anchor endpoint/operation facts cannot be omitted, so an oversized anchor causes a hard failure.

## Size accounting order

The compiler uses deterministic semantics:

```text
1. validate
2. deduplicate
3. sort
4. project
5. omit oversized optional items
6. apply collection cap
7. create coverage
```

This prevents an item from being counted both as `omitted_too_large` and `omitted_by_limit`.

## Observation references

C3a introduced metadata-only observation references containing:

```text
observation_id
project_id
observed_at
actor_id
provenance
response_status
```

They do not contain method, URL, query, headers, body, cookies, authorization, raw request, or raw response.

Observation refs are sorted newest first, then by UUID.

This gives a future planner evidence handles such as a baseline observation ID without automatically giving it raw secrets.

## Lossless FORM/multipart byte projection

FORM and multipart names remain byte-level protocol facts.

C3a does not force UTF-8. It uses `ASCII_BACKSLASH_HEX`:

- printable ASCII is literal
- backslash is escaped
- all other bytes become lowercase `\xhh`

The representation is JSON-safe and lossless.

## Typed JSON paths

JSON path projection uses explicit segment types:

```text
KEY
ARRAY_ITEM
```

Therefore a real key `"ARRAY_ITEM"` cannot collide with the array wildcard.

Likewise `("a.b",)` remains distinct from `("a", "b")`.

## ContextHypothesis

The snapshot does not embed the complete persisted `Hypothesis` object.

Instead it uses an operation-context view containing:

```text
id
project
statement
status
supporting operation witnesses
contradicting operation witnesses
```

This avoids pretending an operation-local snapshot contains the hypothesis's complete evidence set.

At least one selected witness is required, and a witness cannot simultaneously support and contradict the same context hypothesis.

## Structural-only v1 boundary

During design, the initial idea of a strict permanent value-free LLM context was rejected as too restrictive.

Future IDOR/BOLA reasoning may require data such as:

- decoded JWT claims
- tenant IDs
- business object IDs
- ownership relations
- selected scalar values

The v1 decision is narrower:

```text
C3 v1 does not automatically expose RAW or scalar values.
```

It is not:

```text
ReFair reasoning may never use values.
```

The intended future distinction is:

```text
Model may need to KNOW JWT claims.
Model generally does not need to HOLD the reusable bearer token.

Model may need to KNOW object ID 492837.
Model generally does not need an entire raw 80 KiB response.
```

Future evidence retrieval/enrichment will be introduced only when shadow calibration shows it is needed.

## Validation

Baseline:

**226 passed**

Final:

**248 passed**

Permutation smoke:

**equal snapshots**

Non-UTF-8 FORM/multipart JSON serialization smoke:

**passed**

Commit:

`06729fd9caeadcc266f05596c1983d47dfe2e3a7` — `Implemented V0.2-C3a`

C3a was then frozen.

---

# 6. V0.2-C3b — read-only SQLite operation-context assembly

C3b connected the real SQLite state to the C3a compiler.

This was the first integration where ReFair's persisted passive evidence, structural state, deterministic analytics, and bounded context model were exercised together.

Public entry point:

```python
assemble_operation_context(
    repository,
    operation_id,
    *,
    limits=DEFAULT_CONTEXT_LIMITS,
)
```

## Read-only requirement

The assembler requires:

```text
SQLiteRepository(..., read_only=True)
```

A writable repository is rejected.

Context construction therefore cannot accidentally mutate the evidence database.

No context snapshot persistence, analytic processing marker, cache, migration, initialization, or timestamp write is performed.

## Anchor resolution

The caller supplies one `operation_id`.

C3b resolves from SQLite:

```text
HttpOperation
    ↓
ExactEndpoint
    ↓
project_id
```

It does not trust a caller-supplied project or endpoint identity.

Unknown operation raises `ValueError`.

A structurally referenced but missing endpoint raises `RuntimeError` because that represents inconsistent state rather than normal absence.

## New storage read methods

C3b added the minimal read APIs required for correct operation assembly:

```text
get_exact_endpoint
get_http_operation
operation_observation_metadata
operation_json_field_observations
operation_hypothesis_witnesses
```

Existing operation-scoped aggregate readers were reused rather than rewritten.

## RAW-free observation metadata

The observation metadata SQL explicitly selects only:

```text
observation id
project id
observed_at
provenance
actor id
response status
```

It does not select `raw_request` or `raw_response`.

The production assembler therefore does not need to load RAW BLOBs merely to construct context.

This is stronger than filtering RAW after retrieval: RAW is not selected in the first place.

## Same-endpoint expansion

C3b loads sibling operations and method advertisements only for the exact anchor endpoint.

No project-wide or host-wide expansion occurs.

## Existing operation aggregates

C3b reuses the already-established operation-level views for:

```text
query shapes
request representations
response representations
actor outcomes
JSON document outcomes
JSON fields
FORM document outcomes
FORM fields
multipart document outcomes
multipart parts
```

These are all assembled from the anchor operation relation.

## C2 orchestration

C3b is the first layer allowed to invoke C2 because it finally owns a bounded operation context.

It calls exactly the three frozen C2 rule families:

```text
advertised method not observed
duplicate JSON key
multipart disposition ambiguity
```

No fourth analytic rule was added.

No scoring or ranking was added.

## Per-observation JSON facts

Operation-level JSON field aggregates are not sufficient for the duplicate-key C2 rule because its lead identity includes `observation_id`, direction, and path.

C3b therefore added an operation-scoped reader for underlying `JsonFieldObservation` rows.

For C2 it may select only rows where `duplicate_key_observed = true`, because false rows cannot produce that lead.

No raw JSON or scalar values are reconstructed.

## Multipart facts

C3b reuses the operation-scoped multipart part reader.

The same `MultipartPartObservation` collection serves both C3 snapshot structural context and C2 multipart ambiguity derivation.

No second multipart parser or scan exists.

## Hypothesis relation

Hypotheses are related to the anchor operation only through evidence:

```text
hypotheses
    ↓
hypothesis_evidence
    ↓
operation_observations
    ↓
anchor operation
```

C3b does not use hypothesis statement text, endpoint text, host, project membership alone, or semantic similarity.

A hypothesis with evidence only on another operation is excluded.

## Operation-local hypothesis witnesses

Only witnesses from the anchor operation are projected into `ContextHypothesis`.

Example:

```text
H:
    SUPPORTS A on anchor
    SUPPORTS X elsewhere
    CONTRADICTS B on anchor
```

Snapshot receives:

```text
SUPPORTS: A
CONTRADICTS: B
```

and not X.

This does not imply X does not exist globally.

Witness IDs are deduplicated and sorted by UUID before C3a receives them.

This also resolves the C3a nuance where semantically identical witness tuples with different order would otherwise be different objects.

## Coverage preservation

C3b intentionally does not pre-limit operation data.

It passes complete operation-scoped collections to C3a and lets C3a apply the documented limits.

This preserves truthful coverage.

Without that rule:

```text
database operation has 634 fields
C3b secretly selects first 128
C3a reports 128 / 128
```

would falsely look complete.

Instead C3a can truthfully report the omitted facts.

---

# 7. Real SQLite smoke

C3b was validated against the existing local ReFair database in read-only mode.

Selected operation:

```text
07f47f8c-f58b-5f61-9909-ae68543b8b86
```

Observed endpoint:

```text
POST
https://incoming.telemetry.mozilla.org/submit/telemetry/59e41bfa-a0d4-4cb2-9ce7-dc241f02456f/main/Firefox/155.0.1/release/20260903215306
```

Safe structural summary:

```text
siblings:                 0
observation refs:         1 included / 1 available
actor outcomes:           1
JSON documents:           1
JSON fields:              128 included
FORM:                     0
multipart:                0
exploration leads:        3
hypotheses:               0
```

Most importantly:

```text
JSON fields omitted by limit: 1,216
```

Therefore the operation supplied **1,344 unique JSON field facts** to C3a, with only 128 included in the bounded snapshot.

This immediately validated the practical usefulness of explicit coverage.

Without coverage, a future model could incorrectly assume those 128 fields represented the complete observed JSON structure.

## Determinism

The same real operation was assembled twice.

Results:

```text
snapshot equality:    passed
JSON serialization:   passed
serialized equality:  passed
```

## Database integrity

Before and after the read-only smoke:

```text
SQLite schema:       6
observations:        230
exact endpoints:     85
HTTP operations:     87
```

Counts remained unchanged.

No real evidence was modified.

---

# 8. Important lesson from the selected real operation

The deterministic smoke selected a Firefox telemetry operation.

That is not a C3b defect.

C3b is intentionally given an explicit `operation_id` and correctly builds context for that operation.

However, the result demonstrates why future shadow operation selection must not simply do:

```text
pick any operation from the database
```

The passive evidence store contains browser/background traffic as well as target application traffic.

Therefore the first C4 shadow runner should remain explicitly human-anchored:

```text
human selects authorized operation_id
        ↓
C3b
        ↓
OperationContextSnapshot
        ↓
Astra shadow reasoning
```

A future target-scope/application-selection layer may improve this.

It should not be smuggled into C3b.

This is also why global actor-coverage analytics were deferred in C2.

---

# 9. Focused C3b test timing investigation

The C3b implementation initially reported:

```text
258 passed in 129.45s
```

This looked suspicious because C3a had completed its full suite in roughly 6.7 seconds.

A focused rerun was requested:

```powershell
python -m pytest tests/test_context_assembler.py -q --durations=10
```

## First local rerun

The virtual environment was active, but all 10 tests failed during pytest fixture setup with:

```text
PermissionError: [WinError 5]
C:\Users\visce\AppData\Local\Temp\pytest-of-visce
```

Pytest also warned that it could not create:

```text
D:\ReFair\.pytest_cache
```

These were infrastructure/ACL failures.

The tests did not reach ReFair code.

The repeated status `EEEEEEEEEE` represented setup errors, not ten failing C3b assertions.

Changing PowerShell execution policy did not affect the problem because script execution policy was unrelated to filesystem ACLs.

## Elevated PowerShell rerun

The focused test command was rerun from an elevated PowerShell:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_context_assembler.py -q --durations=10
```

Result:

```text
10 passed in 4.78s
```

Slowest test:

```text
1.84s
 test_anchor_lookups_and_unknown_results_are_exact
```

Remaining focused tests were all below half a second.

Conclusion:

The earlier 129-second suite time is not evidence of a C3b performance problem.

The local pytest environment has a Windows temporary-directory / cache ACL issue when run non-elevated.

No production C3b patch is justified from this observation.

Using an elevated shell is acceptable as a temporary diagnostic workaround, but should not become the normal development requirement.

Later cleanup can repair the relevant directory permissions or use an explicitly writable pytest `--basetemp`.

---

# 10. Final architecture state

At the end of 2026-09-10:

```text
Firefox
   ↓
Burp / Montoya
   ↓
collector
   ↓
RAW immutable Observation
   ↓
Normalization
   ↓
Structural extraction
   ↓
operation-scoped L2 state
   ↓
deterministic C2 analytics
   ↓
bounded C3 context
```

More explicitly:

```text
L0 RAW
    Observation
        ↓
L1 NORMALIZED
    NormalizedExchange
        ↓
L2 STRUCTURAL
    ExactEndpoint
    HttpOperation
    ActorOutcome
    query/body shapes
    JSON / FORM / MULTIPART structure
        ↓
L3 DETERMINISTIC ANALYTIC
    ExplorationLead
        ↓
C3
    OperationContextSnapshot
```

The first real integrated path is now:

```text
refair.sqlite3
    ↓
operation_id
    ↓
C3b read-only assembler
    ↓
C2 deterministic rules
    ↓
OperationContextInput
    ↓
C3a deterministic compiler
    ↓
OperationContextSnapshot
```

That path has been validated against real persisted browser/Burp traffic.

---

# 11. What remains deliberately absent

Despite the amount of infrastructure now present, ReFair still does not have:

- Astra integration
- LLM provider code
- prompt rendering
- autonomous operation selection
- active request execution
- policy-engine enforcement on requests
- experiment evaluation
- automatic hypothesis generation by an LLM
- JWT decoding for reasoning context
- business-object value extraction
- IDOR/BOLA ownership modeling
- semantic endpoint graph
- JS route mining
- source-map analysis
- OpenAPI ingestion
- vector database
- embeddings
- generic RAG
- generic crawler
- high-volume fuzzing

This remains intentional.

---

# 12. Next milestone — V0.2-C4

The next planned slice is:

```text
V0.2-C4
Astra shadow planner
+ recording
+ human calibration
+ evaluation snapshots
```

The first shadow mode should not execute HTTP.

Expected flow:

```text
human chooses an authorized operation
        ↓
C3b assembles snapshot
        ↓
Astra receives bounded context
        ↓
Astra proposes:
    ExperimentProposal
    ExplorationProposal
    or WaitDecision
        ↓
proposal recorded
        ↓
human reviews usefulness/correctness
```

No proposal should be automatically executed in C4.

The purpose is calibration.

When Astra makes a poor proposal, the architecture should make it possible to ask:

```text
Was the evidence captured?
Was it represented structurally?
Did C3b select the right operation facts?
Did C3a omit the necessary fact because of a bound?
Was the fact present in the snapshot?
Was the prompt constraint unclear?
Or did the model simply reason badly?
```

This diagnostic separation is one of the main reasons for building C1-C3 before connecting the model.

---

# 13. Future context enrichment note

C3 v1 is intentionally structural and value-free by default.

Shadow testing will determine whether that boundary is sufficient.

Particular attention should be paid to:

- IDOR
- BOLA
- multitenant isolation
- object ownership
- role differences
- authorization claims
- JWT-derived tenant / scope / role information

If Astra repeatedly misses valid hypotheses because the required relation is absent from the snapshot, future context enrichment may introduce controlled evidence-derived facts.

Likely candidates include:

```text
decoded JWT claims
selected business identifiers
ownership relationships
tenant identifiers
selected scalar values
```

Reusable credentials should normally remain local even when their semantic content is exposed to the reasoning layer.

The intended principle is:

```text
Do not dump RAW by default.

Expose exactly the evidence-derived information
that materially improves reasoning.

Keep executable credential material local unless
there is a concrete reason not to.
```

This is deferred until calibration provides evidence that it is necessary.

---

# 14. Commits of the day

```text
e6b9b29aeb74b7e232e11edf2308a57825d26196
Implemented V0.2-C1

77d189bbac9f52afcb3fafdf82ceb9a62daf13bd
Clean up local multipart smoke artifacts

d7305c367462af51bbf1322e15fb52fc72333ec3
Implemented V0.2-C2

06729fd9caeadcc266f05596c1983d47dfe2e3a7
Implemented V0.2-C3a

d73022f8fe02e6d73cd13bf9345bfe684e1e6834
Implemented V0.2-C3b
```

Final pushed HEAD:

```text
d73022f8fe02e6d73cd13bf9345bfe684e1e6834
```

---

# 15. Final status

```text
V0.2-C1   analytic + planner contracts             DONE / FROZEN
V0.2-C2   deterministic analytic leads             DONE / FROZEN
V0.2-C3a  bounded context contracts/compiler       DONE / FROZEN
V0.2-C3b  read-only SQLite context assembly        DONE / FROZEN

V0.2-C4   Astra shadow planner + calibration       NEXT
```

The most important result of the day is not the number of models or tests added.

It is that this path now works on real evidence:

```text
Evidence
    ↓
deterministic structure
    ↓
bounded analytic state
    ↓
bounded working context
```

without yet allowing the probabilistic reasoning layer to directly touch execution.

That is the intended ReFair boundary:

> LLM proposes. Code constrains. Evidence decides. State remembers. Humans authorize dangerous edge cases.
