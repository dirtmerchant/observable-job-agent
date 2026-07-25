# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Job Scout is a LangGraph-based AI agent that matches CVs to jobs with 0–100 fit scores and honest explanations. Every LLM and tool call is traced via Opik. The app runs with zero API keys (Remotive + offline cache fallback).

**Python 3.12+ · uv package manager · Hatchling build backend**

## Common Commands

```bash
make setup       # uv sync + install pre-commit hooks
make test        # pytest (35 tests, offline, no credentials needed)
make lint        # ruff check .
make format      # ruff format . && ruff check --fix .
make app         # launch Gradio UI at http://localhost:7860
make batch       # baseline batch runner (estimates cost first)
make snapshot    # rebuild data/cached_jobs.json from live APIs
make fixtures    # regenerate synthetic CVs in data/fixture_cvs/
```

Run a single test:
```bash
uv run pytest tests/test_nodes.py::test_fetch_jobs_uses_tool_call -v
```

Pre-commit hooks manually:
```bash
uv run pre-commit run --all-files
```

## Architecture

```
CV (PDF) → cv_reader.py → profile.py (LLM → Profile) → runner.py → LangGraph agent → Ranked jobs
```

**LangGraph agent** (`src/job_scout/graph/graph.py`): StateGraph with MemorySaver checkpointer and 3 nodes connected by a conditional reformulation loop:

1. **fetch_jobs** — LLM picks search args via tool call, calls `run_search()`, dedupes against prior loops
2. **rank_jobs** — batches jobs by 5, LLM scores each batch with structured output → `RankedJob[]` sorted by fit
3. **reformulate_query** — broadens the query if <5 jobs scored ≥60 and <2 reformulations have occurred

The loop exits when enough good matches are found or the reformulation cap (2) is hit. LLM calls are capped at 25 per run via `ensure_budget()` in `llm.py`.

**Job source cascade** (`tools/jobs_api.py`): JSearch → Adzuna → Remotive → offline cache. Each implements the `JobSource` protocol. Sources are tried in order; the cache (`data/cached_jobs.json`, ~247 jobs) always works.

**Key data models** (`graph/schemas.py`): `Profile`, `JobPosting`, `JobScore`, `RankedJob`, `TailoringPack` (Phase 2 placeholder). Agent state is a `TypedDict` in `graph/state.py`.

**Tracing** (`tracing.py`): Opik instrumentation wraps the graph, attaches CV text to traces, and registers prompt versions. No-ops gracefully when `OPIK_ENABLED=false`.

**Persistence** (`db.py`): SQLite database stores CVs, profiles, runs, and ranked jobs for the History tab. Configured via `SCOUT_DB_PATH` (default: `data/scout.db`). All writes are best-effort — failures log warnings and don't break the search flow.

**UI** (`app.py`): Gradio 3-step wizard (drop CV → review profile → find jobs) with a History tab to browse past searches. Custom CSS theme.

**LLM factory** (`llm.py`): `get_chat_model("provider:model")` supports `openai:`, `groq:`, `ollama:` prefixes. Default model configured via `SCOUT_MODEL` env var.

## Configuration

All env vars are optional. See `.env.example` for the full list. Key settings in `config.py` (pydantic-settings with `SecretStr` for API keys):

- `SCOUT_MODEL` — LLM provider:model string (default: `openai:gpt-4o-mini`)
- `MAX_LLM_CALLS_PER_RUN` — circuit breaker (default: 25)
- `OPIK_ENABLED` — toggle tracing (default: true)
- `SCOUT_DB_PATH` — SQLite database path (default: `data/scout.db`)
- Job source keys: `JSEARCH_API_KEY`, `ADZUNA_APP_ID`/`ADZUNA_APP_KEY` (all optional)

## Metrics

Prometheus metrics are exposed at `/metrics` on the same port as the Gradio app (7860). All metrics use a dedicated `CollectorRegistry` in `metrics.py` to avoid conflicts with default process collectors. Metric names are prefixed with `job_scout_`. The homelab Helm chart includes a `ServiceMonitor` and Grafana dashboard ConfigMap.

## Testing Conventions

- **All tests run offline** — `conftest.py` forces `OPIK_ENABLED=false` and clears API keys before any imports
- LLMs are mocked using helper fixtures: `structured_llm()`, `tool_calling_llm()`, `plain_llm()`
- HTTP calls mocked with `respx`
- No network access in tests; fixture CVs live in `data/fixture_cvs/`

## Linting (Ruff)

Line length 130, target py312. Selected rules: `I, F, UP, E, W, B, SIM, S`. Notable per-file ignores:
- `src/job_scout/graph/prompts/*` — E501 (long lines OK)
- `tests/*` — S, B relaxed
- `scripts/*` — S relaxed

## Key Constants

- `GOOD_FIT_THRESHOLD = 60` (score cutoff for "good match")
- `MIN_GOOD_JOBS = 5` (quota before stopping reformulation)
- `MAX_REFORMULATIONS = 2` (loop cap)
- `BATCH_SIZE = 5` (jobs per ranking LLM call)
