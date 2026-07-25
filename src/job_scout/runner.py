"""Run orchestration shared by the Gradio app and the batch runner.

One place builds the tracer, wraps the graph, streams node-level status, measures
cost and latency, and attaches the CV to the trace, so the UI and batch paths
cannot drift apart.
"""

from __future__ import annotations

import time
from collections.abc import Iterator
from dataclasses import dataclass, field

from langchain_core.callbacks import UsageMetadataCallbackHandler

from job_scout.config import get_settings
from job_scout.graph import build_graph
from job_scout.graph.schemas import Profile, RankedJob
from job_scout.metrics import (
    JOB_SOURCE_FETCHES,
    JOBS_FETCHED,
    JOBS_RANKED,
    REFORMULATIONS,
    RUN_COST,
    RUN_DURATION,
    RUNS_TOTAL,
    TOP_FIT_SCORE,
)
from job_scout.profile import extract_profile
from job_scout.tracing import attach_cv, get_tracer, opik_url, trace_graph

_PRICES_PER_MTOK: dict[str, tuple[float, float]] = {
    "gpt-4o-mini": (0.15, 0.60),
    "gpt-4o": (2.50, 10.00),
    "gpt-4.1-mini": (0.40, 1.60),
}

_NODE_STATUS = {
    "fetch_jobs": "searching jobs…",
    "rank_jobs": "ranking jobs…",
    "reformulate_query": "broadening the search…",
}


@dataclass
class RunResult:
    """The outcome of one agent run: results plus display/observability fields."""

    profile: Profile | None = None
    ranked_jobs: list[RankedJob] = field(default_factory=list)
    jobs_sources: list[str] = field(default_factory=list)
    reformulation_count: int = 0
    n_jobs_fetched: int = 0
    n_jobs_ranked: int = 0
    errors: list[str] = field(default_factory=list)
    cost_usd: float = 0.0
    latency_s: float = 0.0
    opik_url: str = ""
    failed: bool = False
    error_message: str = ""


def _estimate_cost(usage: dict, model: str) -> float:
    """Estimate USD cost from token usage, for the footer (Opik has the exact figure)."""
    in_price, out_price = _PRICES_PER_MTOK.get(model.split(":", 1)[-1], (0.0, 0.0))
    total = 0.0
    for stats in usage.values():
        total += stats.get("input_tokens", 0) / 1_000_000 * in_price
        total += stats.get("output_tokens", 0) / 1_000_000 * out_price
    return round(total, 6)


def stream_search(
    profile: Profile,
    *,
    cv_text: str = "",
    cv_path: str | None = None,
    thread_id: str,
    tags: list[str],
    selected_job_id: str | None = None,
) -> Iterator[tuple[str, object]]:
    """Run the job-finding graph for an already-extracted profile.

    Yields ``("status", msg)`` per node and finally ``("result", RunResult)``.
    ``selected_job_id`` is passed explicitly on every invocation (default ``None``)
    so a run never routes into stale Phase 2 tailoring on a reused thread.
    """
    settings = get_settings()
    tracer = get_tracer(thread_id, tags)
    usage_cb = UsageMetadataCallbackHandler()
    # track_langgraph already registers the tracer on the graph; passing it in
    # callbacks too would double-fire its run-ID index.
    callbacks = [usage_cb]

    graph = trace_graph(build_graph(), tracer)
    inputs = {"profile": profile, "cv_text": cv_text, "selected_job_id": selected_job_id}
    config = {"configurable": {"thread_id": thread_id}, "callbacks": callbacks, "recursion_limit": 25}

    result = RunResult(opik_url=opik_url(), profile=profile)
    start = time.monotonic()
    try:
        for chunk in graph.stream(inputs, config=config, stream_mode="updates"):
            for node_name, update in chunk.items():
                yield ("status", _status_line(node_name, update))

        final = graph.get_state(config).values
        result.profile = final.get("profile")
        result.ranked_jobs = final.get("ranked_jobs", [])
        result.jobs_sources = final.get("jobs_sources", [])
        result.reformulation_count = final.get("reformulation_count", 0)
        result.n_jobs_fetched = len(final.get("jobs", []))
        result.n_jobs_ranked = len(result.ranked_jobs)
        result.errors = final.get("errors", [])
    except Exception as exc:  # noqa: BLE001 - report as a failed run, keep the trace
        result.failed = True
        result.error_message = f"{type(exc).__name__}: {exc}"
    finally:
        result.latency_s = round(time.monotonic() - start, 2)
        result.cost_usd = _estimate_cost(usage_cb.usage_metadata, settings.scout_model)
        if cv_path:
            attach_cv(tracer, cv_path)
        if tracer:
            tracer.flush()

        # ── Record Prometheus metrics ──────────────────────────────────
        status = "error" if result.failed else "success"
        RUNS_TOTAL.labels(status=status).inc()
        RUN_DURATION.labels(status=status).observe(result.latency_s)
        RUN_COST.observe(result.cost_usd)
        JOBS_FETCHED.observe(result.n_jobs_fetched)
        JOBS_RANKED.observe(result.n_jobs_ranked)
        if result.ranked_jobs:
            TOP_FIT_SCORE.observe(max(rj.fit_score for rj in result.ranked_jobs))
        if result.reformulation_count:
            REFORMULATIONS.inc(result.reformulation_count)
        for source in result.jobs_sources:
            JOB_SOURCE_FETCHES.labels(source=source).inc()

    yield ("result", result)


def _status_line(node_name: str, update: dict) -> str:
    """Human-readable progress line for a completed node."""
    if node_name == "fetch_jobs":
        attempt = update.get("reformulation_count", 0) + 1
        return f"searching jobs (attempt {attempt})… {len(update.get('jobs', []))} found"
    if node_name == "rank_jobs":
        return f"ranking {len(update.get('ranked_jobs', []))} jobs…"
    return _NODE_STATUS.get(node_name, f"{node_name}…")


def run_once(cv_text: str, *, cv_path: str | None = None, thread_id: str, tags: list[str]) -> RunResult:
    """Extract the profile then run the job search, returning the final result.

    Used by the batch runner, which starts from raw CV text.
    """
    profile = extract_profile(cv_text, thread_id=thread_id, tags=tags)
    result = RunResult()
    for kind, payload in stream_search(profile, cv_text=cv_text, cv_path=cv_path, thread_id=thread_id, tags=tags):
        if kind == "result":
            result = payload  # type: ignore[assignment]
    return result
