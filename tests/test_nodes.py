"""Node behaviour with mocked LLMs and search."""

from __future__ import annotations

from unittest.mock import MagicMock

from langchain_core.exceptions import OutputParserException
from pydantic import ValidationError

import job_scout.graph.nodes.fetch_jobs as fetch_mod
import job_scout.graph.nodes.rank_jobs as rank_mod
import job_scout.graph.nodes.reformulate_query as reformulate_mod
from job_scout.graph.nodes.fetch_jobs import fetch_jobs
from job_scout.graph.nodes.rank_jobs import rank_jobs
from job_scout.graph.nodes.reformulate_query import reformulate_query
from job_scout.graph.schemas import JobScore, JobScores
from tests.conftest import make_job, plain_llm, structured_llm, tool_calling_llm


def test_fetch_jobs_uses_llm_tool_args(monkeypatch, sample_profile, sample_jobs):
    llm = tool_calling_llm([{"name": "search_jobs", "args": {"query": "ml engineer", "country": "de", "remote": True}}])
    monkeypatch.setattr(fetch_mod, "get_chat_model", lambda *a, **k: llm)
    captured = {}

    def fake_run_search(query, location, country, remote, limit):
        captured.update(query=query, country=country, remote=remote)
        return sample_jobs, ["adzuna"]

    monkeypatch.setattr(fetch_mod, "run_search", fake_run_search)
    out = fetch_jobs({"profile": sample_profile, "llm_calls": 1})
    assert captured == {"query": "ml engineer", "country": "de", "remote": True}
    assert out["jobs"] == sample_jobs
    assert out["jobs_sources"] == ["adzuna"]
    assert out["search_query"] == "ml engineer"
    assert out["llm_calls"] == 2


def test_fetch_jobs_no_tool_call_fallback(monkeypatch, sample_profile, sample_jobs):
    llm = tool_calling_llm([])  # model issued no tool call
    monkeypatch.setattr(fetch_mod, "get_chat_model", lambda *a, **k: llm)
    monkeypatch.setattr(fetch_mod, "run_search", lambda **k: (sample_jobs, ["cache"]))
    out = fetch_jobs({"profile": sample_profile, "llm_calls": 0})
    assert any("no tool call" in e for e in out["errors"])
    assert out["jobs"] == sample_jobs


def test_rank_jobs_batches_by_five(monkeypatch, sample_profile):
    jobs = [make_job(f"j{i}", f"Role {i}", f"Co{i}") for i in range(7)]
    calls = []

    def fake_model(*a, **k):
        llm = structured_llm(None)

        def invoke(prompt):
            # return a score for whichever ids appear in this batch prompt
            ids = [j.job_id for j in jobs if f"job_id: {j.job_id}\n" in prompt]
            calls.append(len(ids))
            return JobScores(scores=[JobScore(job_id=i, fit_score=80, fit_explanation="ok") for i in ids])

        llm.with_structured_output.return_value.invoke.side_effect = invoke
        return llm

    monkeypatch.setattr(rank_mod, "get_chat_model", fake_model)
    out = rank_jobs({"profile": sample_profile, "jobs": jobs, "llm_calls": 2})
    assert len(calls) == 2  # 7 jobs -> batches of 5 + 2
    assert out["llm_calls"] == 4  # 2 + 2 batches
    assert len(out["ranked_jobs"]) == 7


def test_rank_jobs_empty_jobs(sample_profile):
    out = rank_jobs({"profile": sample_profile, "jobs": [], "llm_calls": 0})
    assert out["ranked_jobs"] == []


def test_reformulate_increments_counter(monkeypatch, sample_profile):
    monkeypatch.setattr(reformulate_mod, "get_chat_model", lambda *a, **k: plain_llm("data analyst"))
    state = {"profile": sample_profile, "search_query": "data scientist", "reformulation_count": 0, "llm_calls": 3}
    out = reformulate_query(state)
    assert out["search_query"] == "data analyst"
    assert out["reformulation_count"] == 1
    assert out["llm_calls"] == 4


def test_rank_jobs_handles_parse_failure(monkeypatch, sample_profile):
    """When structured output fails, the batch is skipped and an error is logged."""
    jobs = [make_job("j1", "Data Scientist", "Acme"), make_job("j2", "ML Engineer", "Globex")]

    def fake_model(*a, **k):
        llm = structured_llm(None)
        llm.with_structured_output.return_value.invoke.side_effect = OutputParserException("bad json")
        return llm

    monkeypatch.setattr(rank_mod, "get_chat_model", fake_model)
    out = rank_jobs({"profile": sample_profile, "jobs": jobs, "llm_calls": 0})
    assert out["ranked_jobs"] == []
    assert out["llm_calls"] == 1
    assert any("structured-output parse failed" in e for e in out["errors"])


def test_rank_jobs_handles_validation_error(monkeypatch, sample_profile):
    """A ValidationError from pydantic is caught the same way."""
    jobs = [make_job("j1", "Data Scientist", "Acme")]

    def fake_model(*a, **k):
        llm = structured_llm(None)
        llm.with_structured_output.return_value.invoke.side_effect = ValidationError.from_exception_data(
            title="JobScores", line_errors=[], input_type="json"
        )
        return llm

    monkeypatch.setattr(rank_mod, "get_chat_model", fake_model)
    out = rank_jobs({"profile": sample_profile, "jobs": jobs, "llm_calls": 0})
    assert out["ranked_jobs"] == []
    assert any("structured-output parse failed" in e for e in out["errors"])


def test_reformulate_handles_llm_failure(monkeypatch, sample_profile):
    """When the LLM call fails, the previous query is kept."""
    failing_llm = MagicMock()
    failing_llm.invoke.side_effect = RuntimeError("model unavailable")
    monkeypatch.setattr(reformulate_mod, "get_chat_model", lambda *a, **k: failing_llm)
    state = {"profile": sample_profile, "search_query": "data scientist", "reformulation_count": 0, "llm_calls": 3}
    out = reformulate_query(state)
    assert out["search_query"] == "data scientist"  # kept previous
    assert out["reformulation_count"] == 1
    assert out["llm_calls"] == 4
    assert any("keeping previous query" in e for e in out["errors"])
