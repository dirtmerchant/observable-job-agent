"""Tests for Prometheus metrics instrumentation."""

from __future__ import annotations

from types import SimpleNamespace

from prometheus_client import generate_latest

import job_scout.runner as runner_mod
from job_scout.graph.schemas import RankedJob
from job_scout.metrics import (
    JOBS_FETCHED,
    JOBS_RANKED,
    PROFILE_EXTRACTIONS,
    REFORMULATIONS,
    REGISTRY,
    RUNS_TOTAL,
)
from job_scout.runner import stream_search


class _FakeGraph:
    def __init__(self, ranked_jobs=None, sources=None, reformulation_count=0, jobs=None):
        self._ranked = ranked_jobs or []
        self._sources = sources or ["cache"]
        self._reformulation_count = reformulation_count
        self._jobs = jobs or []

    def stream(self, inputs, config, stream_mode):
        return iter([])

    def get_state(self, config):
        return SimpleNamespace(
            values={
                "profile": None,
                "ranked_jobs": self._ranked,
                "jobs_sources": self._sources,
                "reformulation_count": self._reformulation_count,
                "jobs": self._jobs,
            }
        )


def _patch(monkeypatch, fake):
    monkeypatch.setattr(runner_mod, "build_graph", lambda: fake)
    monkeypatch.setattr(runner_mod, "trace_graph", lambda g, t: g)
    monkeypatch.setattr(runner_mod, "get_tracer", lambda *a, **k: None)


def _sample_value(metric, labels=None):
    """Read the current value of a metric from the registry.

    Counters expose lines as ``<name>_total{labels} value`` and histograms
    expose ``<name>_count{labels} value`` (for the observation count).
    ``metric._name`` omits the ``_total`` / ``_created`` suffixes that
    prometheus_client adds automatically for counters.
    """
    output = generate_latest(REGISTRY).decode()
    name = metric._name
    is_counter = metric._type == "counter"
    is_histogram = metric._type == "histogram"

    if labels:
        label_str = ",".join(f'{k}="{v}"' for k, v in sorted(labels.items()))
        if is_counter:
            prefix = f"{name}_total{{{label_str}}}"
        elif is_histogram:
            prefix = f"{name}_count{{{label_str}}}"
        else:
            prefix = f"{name}{{{label_str}}}"
    else:
        prefix = f"{name}_count" if is_histogram else f"{name}_total" if is_counter else name

    for line in output.splitlines():
        if line.startswith(prefix + " ") or line.startswith(prefix + "{"):
            return float(line.split()[-1])
    return 0.0


def test_all_metrics_registered_on_registry():
    """Every Job Scout metric must be on the dedicated REGISTRY."""
    output = generate_latest(REGISTRY).decode()
    expected_prefixes = [
        "job_scout_runs_total",
        "job_scout_run_duration_seconds",
        "job_scout_run_cost_usd",
        "job_scout_jobs_fetched",
        "job_scout_jobs_ranked",
        "job_scout_top_fit_score",
        "job_scout_profile_extractions_total",
        "job_scout_reformulations_total",
        "job_scout_job_source_fetches_total",
    ]
    for prefix in expected_prefixes:
        assert any(line.startswith(prefix) or line.startswith(f"# HELP {prefix}") for line in output.splitlines()), (
            f"Metric {prefix} not found in REGISTRY output"
        )


def test_successful_run_increments_counters(monkeypatch, sample_profile):
    """A successful run should increment runs_total{status=success}."""
    fake = _FakeGraph()
    _patch(monkeypatch, fake)

    before = _sample_value(RUNS_TOTAL, {"status": "success"})
    list(stream_search(sample_profile, thread_id="t-metrics-1", tags=["test"]))
    after = _sample_value(RUNS_TOTAL, {"status": "success"})

    assert after == before + 1.0


def test_failed_run_increments_error_counter(monkeypatch, sample_profile):
    """A run that raises should increment runs_total{status=error}."""

    class _BrokenGraph(_FakeGraph):
        def stream(self, inputs, config, stream_mode):
            raise RuntimeError("boom")

    _patch(monkeypatch, _BrokenGraph())

    before = _sample_value(RUNS_TOTAL, {"status": "error"})
    events = list(stream_search(sample_profile, thread_id="t-metrics-2", tags=["test"]))
    after = _sample_value(RUNS_TOTAL, {"status": "error"})

    assert after == before + 1.0
    result = events[-1][1]
    assert result.failed is True


def test_run_records_histogram_observations(monkeypatch, sample_profile, sample_jobs):
    """Histograms for jobs_fetched, jobs_ranked, and cost should get observations."""
    from job_scout.graph.schemas import JobPosting

    jobs = [
        JobPosting(
            job_id=f"j{i}", title=f"Job {i}", company="Co", location="Berlin",
            remote=False, description="desc", url="https://example.com", tags=[], source="cache",
        )
        for i in range(3)
    ]
    ranked = [
        RankedJob(job=jobs[0], fit_score=85, fit_explanation="good", matched_skills=["python"], gaps=[]),
    ]
    fake = _FakeGraph(ranked_jobs=ranked, jobs=jobs)
    _patch(monkeypatch, fake)

    fetched_before = _sample_value(JOBS_FETCHED)
    ranked_before = _sample_value(JOBS_RANKED)
    list(stream_search(sample_profile, thread_id="t-metrics-3", tags=["test"]))
    fetched_after = _sample_value(JOBS_FETCHED)
    ranked_after = _sample_value(JOBS_RANKED)

    assert fetched_after == fetched_before + 1.0
    assert ranked_after == ranked_before + 1.0


def test_profile_extraction_increments_counter(monkeypatch):
    """Successful profile extraction increments the counter."""
    from unittest.mock import MagicMock

    from job_scout.graph.schemas import Profile
    from job_scout.profile import extract_profile

    mock_profile = Profile(
        name="Test",
        seniority="mid",
        primary_roles=["Dev"],
        skills=["python"],
        years_experience=3,
        locations=["NYC"],
        languages=["English"],
        remote_ok=True,
        raw_summary="A dev.",
    )
    mock_model = MagicMock()
    mock_model.with_structured_output.return_value.invoke.return_value = mock_profile
    monkeypatch.setattr("job_scout.profile.get_chat_model", lambda *a, **k: mock_model)

    before = _sample_value(PROFILE_EXTRACTIONS, {"status": "success"})
    extract_profile("some cv text")
    after = _sample_value(PROFILE_EXTRACTIONS, {"status": "success"})

    assert after == before + 1.0


def test_metrics_endpoint_format():
    """The /metrics output should be valid Prometheus exposition format."""
    output = generate_latest(REGISTRY)
    text = output.decode("utf-8")
    # Must contain at least one HELP and TYPE line
    assert "# HELP job_scout_runs_total" in text
    assert "# TYPE job_scout_runs_total counter" in text
    # Must contain our metric prefix
    assert "job_scout_" in text


def test_reformulation_counter_incremented(monkeypatch, sample_profile):
    """Reformulation count from the run should increment the counter."""
    fake = _FakeGraph(reformulation_count=2, sources=["remotive"])
    _patch(monkeypatch, fake)

    before = _sample_value(REFORMULATIONS)
    list(stream_search(sample_profile, thread_id="t-metrics-4", tags=["test"]))
    after = _sample_value(REFORMULATIONS)

    assert after == before + 2.0
