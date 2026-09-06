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

## V0

This repository currently provides only the deterministic foundation:

- typed Pydantic models for projects, actors, evidence, interpretations,
  experiments, policy outcomes, budgets, usage, and run state;
- immutable raw observations and content-addressed assets in a small SQLite
  repository;
- SHA-256 asset identity based on decompressed response bytes, independent of URL;
- separate API/model and HTTP budget accounting with warnings and hard stops;
- a one-slot async guard specifically for future agent-generated active traffic;
- validated YAML configuration and invariant-focused tests.

V0 deliberately does **not** send HTTP requests, crawl, scan, exploit, automate a
browser, call an LLM, integrate Burp/MCP, use RAG/vector storage, provide a UI, or
implement multi-agent behavior. It does not yet contain a complete scope or
authorization policy engine.

## Layout

```text
refair/
  assets/       content-based static asset identity
  models/       domain, budget, experiment, and run-state models
  policy/       deterministic budget and active-concurrency controls
  storage/      explicit SQLite schema and repository
  config.py     strict YAML configuration loading
tests/          invariant-oriented tests
```

`config.example.yaml` reserves listener 8081 for normal/manual Burp use and uses
8082/8083 for two isolated example actors. Actor tenant identity starts as unknown
and must be established from evidence later.

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

The tests use temporary SQLite databases and perform no network activity.
