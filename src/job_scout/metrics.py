"""Prometheus metrics for Job Scout.

All metrics live on a dedicated ``CollectorRegistry`` so they never conflict
with the default process-level collectors that ``prometheus_client`` registers
automatically.  The Gradio app exposes ``/metrics`` using this registry.
"""

from __future__ import annotations

from prometheus_client import CollectorRegistry, Counter, Histogram

REGISTRY = CollectorRegistry()

# ── Run-level metrics ──────────────────────────────────────────────────────

RUNS_TOTAL = Counter(
    "job_scout_runs_total",
    "Total agent runs",
    ["status"],
    registry=REGISTRY,
)

RUN_DURATION = Histogram(
    "job_scout_run_duration_seconds",
    "End-to-end run latency",
    ["status"],
    buckets=(1, 2, 5, 10, 20, 30, 60, 120, 300),
    registry=REGISTRY,
)

RUN_COST = Histogram(
    "job_scout_run_cost_usd",
    "Estimated LLM cost per run (USD)",
    buckets=(0.001, 0.005, 0.01, 0.02, 0.05, 0.1, 0.25, 0.5, 1.0),
    registry=REGISTRY,
)

JOBS_FETCHED = Histogram(
    "job_scout_jobs_fetched",
    "Jobs fetched per run",
    buckets=(1, 5, 10, 20, 50, 100, 200, 500),
    registry=REGISTRY,
)

JOBS_RANKED = Histogram(
    "job_scout_jobs_ranked",
    "Jobs ranked per run",
    buckets=(1, 5, 10, 20, 50, 100),
    registry=REGISTRY,
)

TOP_FIT_SCORE = Histogram(
    "job_scout_top_fit_score",
    "Best fit score per run",
    buckets=(10, 20, 30, 40, 50, 60, 70, 80, 90, 100),
    registry=REGISTRY,
)

# ── Component-level metrics ────────────────────────────────────────────────

PROFILE_EXTRACTIONS = Counter(
    "job_scout_profile_extractions_total",
    "Profile extraction attempts",
    ["status"],
    registry=REGISTRY,
)

REFORMULATIONS = Counter(
    "job_scout_reformulations_total",
    "Query reformulation loops executed",
    registry=REGISTRY,
)

JOB_SOURCE_FETCHES = Counter(
    "job_scout_job_source_fetches_total",
    "Jobs fetched by source",
    ["source"],
    registry=REGISTRY,
)
