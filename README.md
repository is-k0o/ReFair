# ReFair

ReFair is intended to become a controlled, AI-assisted web penetration-testing and
bug-bounty research system. It is for authorized security testing only. A future
version may observe traffic from dedicated Firefox profiles through Burp Suite,
maintain structured application state, and allow a language model to propose
testable experiments. Deterministic code—not a model—will remain responsible for
scope, execution, concurrency, rate limits, budgets, evidence, and mechanical
state transitions.

> LLM proposes. Code constrains. Evidence decides. State remembers. Humans
> authorize dangerous edge cases.

## V0.1.5

This repository provides the deterministic foundation plus the first passive
Burp/Montoya ingestion bridge:

- typed Pydantic models for projects, actors, evidence, interpretations,
  experiments, policy outcomes, budgets, usage, and run state;
- immutable raw observations and content-addressed assets in a small SQLite
  repository;
- SHA-256 asset identity based on decompressed response bytes, independent of URL;
- separate API/model and HTTP budget accounting with warnings and hard stops;
- a one-slot async guard specifically for future agent-generated active traffic;
- validated YAML configuration and invariant-focused tests;
- a localhost-only Python collector that turns completed proxy exchanges into
  immutable `Observation` rows;
- a thin Java 21 Montoya extension that observes completed proxy responses,
  serializes exact request/response bytes with base64, and hands events to a
  bounded asynchronous HTTP/1.1 transport worker;
- a read-only command-line inspector for safe summaries, recent observation
  metadata, and explicitly bounded raw previews.

V0.1.5 does **not** generate active traffic, crawl, scan, exploit, automate a
browser, call an LLM or Burp AI, use MCP/RAG/vector storage, normalize traffic,
extract endpoints, provide a UI, or implement multi-agent behavior. It does not
yet contain a complete scope or authorization policy engine.

The bridge is passive and fail-open relative to browser traffic. Burp callbacks
never wait for the collector. Events enter a bounded in-memory queue and receive
one short transport attempt on a background thread. Collector failures,
oversized exchanges, full queues, and unload-time drops are logged; there are no
hidden or infinite retries.

Only completed exchanges are ingested. A request that never receives a response
is not persisted in V0.1.5 because raw observations are never created incomplete
and updated later.

## Bridge boundary

```text
Firefox
  -> Burp actor listener
  -> passive Montoya response callback
  -> bounded background transport
  -> HTTP/1.1 + JSON on 127.0.0.1:8765
  -> Python collector
  -> immutable Observation
  -> SQLite
```

The Java envelope contains the listener port and raw messages, but no trusted
actor or provenance. Python maps configured actor listener ports to actor IDs and
always assigns `BROWSER` provenance. Unconfigured listeners receive an observable
`202 ignored` response and create no evidence.

Port **8081 is external human Burp usage and is not owned or configured by
ReFair**. The example ReFair actors use 8082 and 8083. Port 8765 is only the local
Java-to-Python bridge transport; it is not a Burp proxy listener.

## Layout

```text
refair/
  assets/       content-based static asset identity
  bridge/       strict passive collector transport and CLI
  inspect/      read-only observation inspection CLI
  models/       domain, budget, experiment, and run-state models
  policy/       deterministic budget and active-concurrency controls
  storage/      explicit SQLite schema and repository
  config.py     strict YAML configuration loading
tests/          invariant-oriented tests
burp-extension/ Java 21 passive Montoya adapter and Gradle build
```

`config.example.yaml` contains a stable project UUID, actor listener/profile
mapping, loopback collector address, bridge port, and SQLite path. Actor tenant
identity starts as unknown and must be established from evidence later.

## Install

Python 3.12 or newer is required.

```bash
python -m venv .venv
source .venv/bin/activate  # Windows PowerShell: .venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
```

## Test

```bash
python -m pytest
```

The automated tests use temporary SQLite databases and require no Burp instance
or external network access. The Java HTTP/1.1 regression test uses only a local
loopback socket.

Build and test the Montoya extension with Java 21:

```powershell
cd burp-extension
.\gradlew.bat clean test jar
```

On macOS/Linux, use `./gradlew clean test jar`. The loadable JAR is written to
`burp-extension/build/libs/refair-burp-extension-0.1.5.jar`.

## Inspect observations

The inspector reads the SQLite path from the same ReFair configuration as the
collector and opens it read-only:

```powershell
refair-inspect --config config.example.yaml summary
refair-inspect --config config.example.yaml list --limit 20
refair-inspect --config config.example.yaml show <observation-uuid>
```

The equivalent module form is `python -m refair.inspect`. `list` omits query
strings by default and supports `--actor`, `--provenance`, `--method`, `--status`,
and `--full-url` filters/options. `show` prints metadata and byte counts, never raw
HTTP by default.

Raw evidence may contain credentials, cookies, session tokens, personal data, or
other sensitive content. A byte-safe preview must be explicitly requested and is
hard-capped at 4096 bytes per message:

```powershell
refair-inspect --config config.example.yaml show <observation-uuid> --raw-preview 500
```

This is bounded presentation, not redaction; immutable stored evidence is never
changed.

## Manual smoke test

From the repository root, install ReFair and start the collector:

```powershell
python -m pip install -e ".[dev]"
python -m refair.bridge --config config.example.yaml
```

In a second terminal, build the extension:

```powershell
cd burp-extension
.\gradlew.bat clean test jar
```

Then:

1. In Burp, open **Extensions > Installed > Add**, choose Java, and select
   `burp-extension/build/libs/refair-burp-extension-0.1.5.jar`.
2. Keep the existing human listener on 8081 outside ReFair. Configure actor
   listeners 8082 and 8083, and point Firefox profiles `example-ai-a` and
   `example-ai-b` at their respective listeners.
3. Use explicit marker paths when repeating the smoke test:

   ```powershell
   # Actor A profile through 8082
   https://example.com/refair-smoke-actor-a

   # Actor B profile through 8083
   https://example.com/refair-smoke-actor-b
   ```

4. Inspect attribution and counts without modifying evidence:

   ```powershell
   refair-inspect --config config.example.yaml summary
   refair-inspect --config config.example.yaml list --limit 20
   refair-inspect --config config.example.yaml show <observation-uuid>
   ```

5. Use `show <uuid> --raw-preview 500` only when bounded raw-byte verification is
   required.
6. Send traffic through 8081 and rerun `summary`. The observation
   count must not increase.

This Firefox/Burp/collector flow was manually validated on Windows for both
actors: Firefox A through 8082 was attributed to `actor_a`, and Firefox B through
8083 was attributed to `actor_b`, both with `BROWSER` provenance and preserved
Montoya request/response bytes. This live check is manual and is distinct from
the automated Python and Java suites.

The extension defaults to
`http://127.0.0.1:8765/v1/observations/passive`. If the configured bridge port is
changed, start Burp with the matching JVM property
`-Drefair.collector.url=http://127.0.0.1:<port>/v1/observations/passive`.
