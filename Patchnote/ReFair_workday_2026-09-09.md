# ReFair — Workday patchnote — 2026-09-09

## Summary

Today focused on finishing and empirically validating the passive structural body-modeling slice of V0.2.

The main result is that ReFair now has bounded deterministic structural extraction for:

- JSON
- `application/x-www-form-urlencoded`
- `multipart/form-data`

without storing application values in derived structural state.

The work stayed within the existing architectural rule:

> LLM proposes. Code constrains. Evidence decides. State remembers. Humans authorize dangerous edge cases.

No LLM, active replay, crawler, browser automation, policy-engine expansion, semantic inference, route inference, entity inference, or multipart exploitation logic was added.

At the end of the day:

- public package version remains `0.1.5`
- `NORMALIZER_VERSION = 2`
- SQLite schema version is `6`
- `STRUCTURAL_VERSION = 5`
- Python test suite: **145 passed**
- current pushed `main`: `0795eb8e63365bef8b467f8159d3f37c100d4821` — `Implemented V0.2-B2c`

---

# 1. V0.2-B2a — Content-Encoding stabilization

Before continuing into other body formats, the JSON structural extractor was hardened against protocol-level representation encoding.

The local dataset had exposed two classes of nominal `application/json` bodies that strict JSON parsing could not interpret directly:

- Mozilla telemetry requests using `Content-Encoding: gzip`
- Google responses carrying an anti-XSSI prefix before otherwise JSON-like data

The first was a parser-layering problem. The second was not.

## Implemented behavior

Added bounded representation decoding before JSON structural parsing.

Supported:

- no `Content-Encoding`
- `identity`
- `gzip`
- `x-gzip`

Explicit failure states were added for:

- `UNSUPPORTED_CONTENT_ENCODING`
- `CONTENT_DECODING_FAILED`

Decoding remains bounded.

`br`, `zstd`, stacked encodings, and generic decompression machinery were deliberately not added.

The anti-XSSI Google responses remain `MALFORMED`, intentionally: ReFair does not strip arbitrary application-specific prefixes merely to make data parse as JSON.

## Result

Real local observations moved from:

- JSON parsed: 8
- JSON malformed: 20

to:

- JSON parsed: 18
- JSON malformed: 10

All 10 Mozilla gzip observations moved from malformed to parsed.

The 10 Google anti-XSSI observations remained malformed.

This was treated as evidence that the protocol-decoding patch fixed a real representation-layer issue without adding speculative application heuristics.

Commit:

`709479cbbc2ab9213a5735ee0272c4cb0c049daf` — `Implemented the V0.2-B2a Content-Encoding patch`

B2a was then frozen.

---

# 2. V0.2-B2b — application/x-www-form-urlencoded

B2b implemented deterministic structural extraction for `application/x-www-form-urlencoded`.

## Structural model

Parsing is performed only when normalization reports `BodyKind.FORM`.

Both request and response directions are supported.

Field identity is byte-level and value-free.

Canonical form field names are the decoded field-name octets after form-component decoding:

- `+` -> space byte
- valid `%HH` -> decoded byte
- malformed/incomplete `%` sequences remain literal

Field names are stored as SQLite `BLOB`.

No Unicode interpretation is forced.

Values are discarded from derived structural state.

## Segment semantics

The parser:

- splits on `&`
- does not treat `;` as a separator
- splits each non-empty segment on the first `=` only
- distinguishes bare from assigned occurrences
- preserves explicit empty field names
- aggregates repeated fields per observation

For each field ReFair stores:

- `occurrence_count`
- `assigned_occurrence_count`

No form values, value hashes, or previews are persisted.

## Bounds

Implemented bounded parsing for:

- raw body: 1 MiB
- decoded body: 1 MiB
- actual field occurrences: 10,000
- decoded field name: 16 KiB

All-or-nothing semantics are used when a structural bound is exceeded.

## Storage

Schema bumped:

`4 -> 5`

Structural version:

`3 -> 4`

Added:

- `form_documents`
- `form_field_observations`

Added JOIN-based operation read views rather than persistent per-operation summary tables.

The JSON and FORM extractors now share one private bounded representation-decoding path for `identity` / `gzip` / `x-gzip`.

## Validation

Baseline:

**102 passed**

Final:

**119 passed**

Migration, reprocessing, idempotence, and JSON regression behavior were validated.

The existing local dataset initially contained no FORM observations, so synthetic correctness alone was not considered enough to freeze B2b.

### Real browser smoke

A real Firefox -> Burp -> Montoya -> collector -> SQLite -> processor form POST to:

`https://httpbin.org/post`

was captured using `application/x-www-form-urlencoded`.

Observed structural result:

- document direction: `REQUEST`
- parse status: `PARSED`
- expected exact form field names
- repeated `topping` field:
  - `occurrence_count = 2`
  - `assigned_occurrence_count = 2`
- no form values in derived state

This gave B2b both synthetic and empirical validation.

Commit:

`ff35cbca82cc81aecd74ef65a57ab7ee98feb8cc` — `Implemented V0.2-B2b`

B2b was frozen.

---

# 3. Operational smoke-test fix: stale Burp extension path

During the B2b smoke test, Burp traffic was visible on listener 8083 but ReFair was not receiving observations.

Root cause:

Burp was still configured to load:

`refair-burp-extension-0.1.0.jar`

while the current Gradle build produces:

`refair-burp-extension-0.1.5.jar`

The extension was rebuilt/reloaded from the correct path.

After reload, the expected bridge message appeared and passive exchanges were again sent to the collector.

No code change was required.

This confirmed the issue was local Burp extension configuration, not the B2b parser.

---

# 4. V0.2-B2c — multipart/form-data

B2c implemented bounded deterministic structural extraction for `multipart/form-data`.

This was the most protocol-sensitive B2 slice because multipart contains ordered real parts, per-part headers, boundaries, filenames, and arbitrary binary content.

The implementation deliberately models syntax and evidence only.

It does not infer what a part semantically "means".

## Scope gate

Extraction occurs only when:

- normalized body kind is `MULTIPART`
- normalized top-level media type is exactly `multipart/form-data`

Top-level:

- `multipart/mixed`
- `multipart/related`
- `multipart/alternative`

remain outside B2c.

Both request and response directions are supported.

---

# 5. Multipart structural model

A multipart body is represented as:

- one `MultipartDocument`
- ordered actual part occurrences
- exact observed `name=` facts for each part

Parts use zero-based `part_index` in wire order.

`part_index` is only an occurrence fact. It is not treated as semantic identity across observations.

## Exact name facts

Multipart `name=` is treated as an exact protocol fact, not as semantic truth.

Examples such as:

- `avatar`
- `file`
- `document`
- `user[avatar]`
- `files[]`
- `documents[123]`

remain exact observed byte strings.

B2c does not infer arrays, object structure, dynamic IDs, field roles, or semantic equivalence.

Unlike urlencoded forms:

- `+` is not decoded
- `%20` is not decoded
- `%2F` is not decoded

Multipart names are stored as exact byte values in SQLite `BLOB` rows after only quoted-string unescaping.

## Duplicate name parameters

B2c deliberately does not select one "canonical" value from malformed or ambiguous syntax such as:

`name="a"; name="b"`

Instead it records:

- `name_parameter_count`
- each distinct exact name
- per-name `occurrence_count`

This preserves evidence without silently choosing first-wins or last-wins parser semantics.

---

# 6. Filename handling

Filename presence is preserved because it is security-relevant.

Filename values are not.

For each part ReFair stores:

- `filename_parameter_count`
- `filename_empty_count`
- `filename_nonempty_count`

Invariant:

`filename_parameter_count == filename_empty_count + filename_nonempty_count`

This distinguishes:

- filename absent
- `filename=""`
- non-empty `filename="..."`

without persisting the actual filename.

This design is useful for later web-security reasoning while avoiding value leakage and unnecessary cardinality.

`filename*` semantics remain deliberately unimplemented.

---

# 7. Part Content-Type

For each part B2c may store the observed normalized media type.

Example:

`Content-Type: Image/PNG; charset=whatever`

becomes:

`image/png`

Parameters such as charset are not persisted.

Missing part Content-Type remains `None`.

A part carrying:

- `application/json`
- `multipart/mixed`

is recorded only as having that observed media type.

B2c does not recursively parse the body as JSON or nested multipart.

---

# 8. Multipart framing and boundary parsing

The top-level multipart boundary is read from immutable RAW HTTP headers.

Supported:

- token boundary values
- quoted boundary values
- quoted-string escaping

Rejected or bounded:

- missing boundary
- empty boundary
- duplicate boundary parameter
- ambiguous multiple top-level Content-Type headers
- CR/LF in boundary
- oversized boundary

The boundary value itself is not persisted.

Multipart framing is line-aware rather than using naive:

`body.split(b"--" + boundary)`

This avoids splitting merely because boundary-looking bytes occur inside a file body.

The parser:

- supports CRLF framing
- accepts LF-only framing
- ignores preamble / epilogue
- requires a closing boundary
- accepts an empty valid multipart document
- uses all-or-nothing behavior on malformed input

A possible future hardening question remains whether LF-only boundary recognition should stay permissive inside otherwise CRLF traffic. No patch was made because no real failure currently justifies changing the behavior.

---

# 9. Multipart limits

Implemented deterministic safety bounds:

- raw body: 1 MiB
- decoded body: 1 MiB
- parts: 1,000
- boundary: 1 KiB
- top/part headers: 64 KiB
- exact name: 16 KiB
- Content-Disposition parameters: 256

Raw/decoded body overflow maps to:

`SKIPPED_TOO_LARGE`

Structural parser bounds map to:

`LIMIT_EXCEEDED`

Malformed syntax maps to:

`MALFORMED`

No partial part/name rows are persisted after failure.

Existing representation decoding is reused for:

- absent / identity
- gzip
- x-gzip

with existing explicit failure states for unsupported/corrupt encodings.

---

# 10. Multipart persistence

Schema bumped:

`5 -> 6`

Structural version:

`4 -> 5`

Added:

- `multipart_documents`
- `multipart_parts`
- `multipart_part_names`

No JSON, FORM, or B1 tables were rebuilt.

No persistent per-operation multipart summary table was added.

Operation-level multipart state is queried through evidence-backed JOINs.

Structural writes remain atomic with the rest of the observation projection.

No raw evidence mutation occurred.

---

# 11. B2c validation

Baseline:

**119 passed**

Final:

**145 passed**

Focused structural/migration suite:

**78 passed**

Also validated:

- processor help
- `git diff --check`
- schema `5 -> 6`
- structural version `4 -> 5`
- first reprocessing
- immediate second-run idempotence
- existing derived-state fingerprints
- observation immutability triggers
- existing JSON and FORM behavior

The original DB initially had no multipart/form-data observations, so B2c was not frozen from synthetic tests alone.

---

# 12. Real multipart browser smokes

A local HTML multipart form was used with Firefox through the ReFair actor Burp listener and posted to:

`https://httpbin.org/post`

The chain exercised was:

Firefox -> Burp -> Montoya extension -> collector -> immutable Observation -> normalization -> structural extraction

## Smoke A — empty file input

Observed multipart structure:

- `note`
- `upload`
- `tag`
- `tag`

The `upload` part contained:

- `filename_parameter_count = 1`
- `filename_empty_count = 1`
- `filename_nonempty_count = 0`
- `Content-Type: application/octet-stream`

This empirically validated the `PRESENT_EMPTY` filename state.

The two `tag` fields were preserved as two actual ordered multipart part occurrences rather than prematurely aggregated into one part.

## Collector restart issue during migration

After the DB had been migrated to schema 6, the already-running collector process still had schema version 5 loaded in memory.

It consequently emitted:

`UnsupportedSchemaVersionError: database schema version 6 is newer than supported version 5`

and some bridge events returned HTTP 500.

The collector was restarted.

No code bug was involved: the process was simply stale after an in-place development migration.

The bridge intentionally performs no hidden retries, so events that failed during this interval were correctly treated as dropped.

## Smoke B — real selected file

A real PNG was then selected in the multipart file input.

The structural result for the `upload` part was:

- `content_type = image/png`
- `filename_parameter_count = 1`
- `filename_empty_count = 0`
- `filename_nonempty_count = 1`
- exact part name = `upload`

The actual filename was not present in multipart-derived state.

This empirically validated the `PRESENT_NONEMPTY` filename state and real browser file-part Content-Type handling.

At this point B2c had:

- synthetic parser coverage
- migration coverage
- idempotence coverage
- real browser framing
- real browser boundary
- real text fields
- repeated same-name parts
- real empty filename
- real non-empty filename
- real file media type
- no filename-value persistence
- no part-content persistence

B2c was therefore frozen.

Commit:

`0795eb8e63365bef8b467f8159d3f37c100d4821` — `Implemented V0.2-B2c`

---

# 13. Current architecture after B2

The passive structural path is now:

```text
RAW Observation
    |
    v
NormalizedExchange
    |
    +--> exact endpoint / HTTP operation
    |
    +--> passive method advertisements
    |
    +--> JSON structural facts
    |
    +--> application/x-www-form-urlencoded structural facts
    |
    +--> multipart/form-data structural facts
```

The important boundary is unchanged:

```text
RAW evidence
    -> exact immutable truth

derived structure
    -> bounded, deterministic, value-minimized facts

semantic interpretation
    -> deferred
```

ReFair still does not infer:

- what a multipart field "really represents"
- entities
- tenant relationships
- routes/templates
- workflows
- vulnerabilities
- exploitability

Those belong to later layers.

---

# 14. Explicitly frozen state

The following slices should now remain untouched unless real traffic exposes a concrete failure:

- V0.2-A normalization
- V0.2-B1 exact endpoint / operation model
- V0.2-B2a JSON structure
- V0.2-B2b urlencoded structure
- V0.2-B2c multipart/form-data structure

The preferred rule from here is:

> Do not improve parsers speculatively. Patch only evidence-backed failures.

This avoids turning ReFair into a generic MIME/HTTP parsing project.

---

# 15. Deferred work

Still deliberately out of scope:

- multipart nested recursion
- `filename*`
- MIME sniffing
- file extension inference
- file hashes / previews / content parsing
- JSON parsing inside multipart parts
- top-level `multipart/mixed`
- Content-Transfer-Encoding
- dynamic-name normalization
- semantic input identity
- route inference
- entity inference
- data-flow inference
- workflow inference
- context compiler
- analytic state
- Astra / LLM integration
- ExperimentProposal
- policy-engine expansion
- active executor / replay
- crawler
- browser automation

---

# 16. End-of-day state

Current pushed `main`:

`0795eb8e63365bef8b467f8159d3f37c100d4821`

Commit message:

`Implemented V0.2-B2c`

Current versions:

```text
package                0.1.5
SQLite schema          6
NORMALIZER_VERSION     2
STRUCTURAL_VERSION     5
tests                   145 passed
```

The passive body-structure layer is now broad enough for the intended V0 architecture.

Next work should start above this layer rather than continuing to expand body parsers without evidence.
