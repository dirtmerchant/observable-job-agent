"""Tests for the SQLite persistence layer (job_scout.db).

All tests use in-memory SQLite — no disk IO, fully offline.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field

from job_scout import db
from job_scout.graph.schemas import JobPosting, Profile, RankedJob


def _conn() -> sqlite3.Connection:
    """Create a fresh in-memory database with the schema applied."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    db.init_db(conn)
    return conn


def _profile() -> Profile:
    return Profile(
        name="Ada Lovelace",
        seniority="senior",
        primary_roles=["Software Engineer", "ML Engineer"],
        skills=["python", "pytorch", "sql"],
        years_experience=8.0,
        locations=["London, UK"],
        languages=["English", "French"],
        remote_ok=True,
        raw_summary="Experienced engineer.",
    )


def _ranked_jobs() -> list[RankedJob]:
    return [
        RankedJob(
            job=JobPosting(
                job_id="j1", source="cache", title="ML Engineer",
                company="Acme", location="London", remote=True,
                description="Build models.", url="https://example.com/j1",
                tags=["ml", "python"],
            ),
            fit_score=85,
            fit_explanation="Strong ML background.",
            matched_skills=["python", "pytorch"],
            gaps=["kubernetes"],
        ),
        RankedJob(
            job=JobPosting(
                job_id="j2", source="remotive", title="Data Analyst",
                company="Globex", location="Remote", remote=True,
                description="Analyze data.", url="https://example.com/j2",
                tags=["sql"],
            ),
            fit_score=62,
            fit_explanation="Good SQL skills, missing Tableau.",
            matched_skills=["sql"],
            gaps=["tableau"],
        ),
    ]


@dataclass
class _FakeRunResult:
    """Mimics RunResult for save_run without importing runner (avoids circular deps)."""

    ranked_jobs: list[RankedJob] = field(default_factory=list)
    jobs_sources: list[str] = field(default_factory=list)
    reformulation_count: int = 0
    n_jobs_fetched: int = 0
    n_jobs_ranked: int = 0
    cost_usd: float = 0.0
    latency_s: float = 0.0
    opik_url: str = ""
    failed: bool = False
    error_message: str = ""
    errors: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_save_cv_roundtrip():
    conn = _conn()
    cv_id = db.save_cv("Hello world CV text", "resume.pdf", conn=conn)
    assert cv_id
    row = conn.execute("SELECT * FROM cvs WHERE cv_id = ?", (cv_id,)).fetchone()
    assert row["filename"] == "resume.pdf"
    assert row["cv_text"] == "Hello world CV text"


def test_save_profile_roundtrip():
    conn = _conn()
    cv_id = db.save_cv("cv text", "cv.pdf", conn=conn)
    profile = _profile()
    profile_id = db.save_profile(profile, cv_id, conn=conn)
    assert profile_id

    loaded = db.get_profile(profile_id, conn=conn)
    assert loaded is not None
    assert loaded.name == "Ada Lovelace"
    assert loaded.seniority == "senior"
    assert loaded.primary_roles == ["Software Engineer", "ML Engineer"]
    assert loaded.skills == ["python", "pytorch", "sql"]
    assert loaded.years_experience == 8.0
    assert loaded.locations == ["London, UK"]
    assert loaded.languages == ["English", "French"]
    assert loaded.remote_ok is True
    assert loaded.raw_summary == "Experienced engineer."


def test_get_profile_unknown_returns_none():
    conn = _conn()
    assert db.get_profile("nonexistent", conn=conn) is None


def test_save_run_and_list_runs():
    conn = _conn()
    cv_id = db.save_cv("cv", "cv.pdf", conn=conn)
    profile_id = db.save_profile(_profile(), cv_id, conn=conn)
    ranked = _ranked_jobs()
    result = _FakeRunResult(
        ranked_jobs=ranked,
        jobs_sources=["cache", "remotive"],
        n_jobs_fetched=10,
        n_jobs_ranked=2,
        cost_usd=0.0012,
        latency_s=3.5,
    )
    db.save_run("run-1", profile_id, result, model="openai:gpt-4o-mini", conn=conn)

    runs = db.list_runs(conn=conn)
    assert len(runs) == 1
    assert runs[0]["run_id"] == "run-1"
    assert runs[0]["profile_name"] == "Ada Lovelace"
    assert runs[0]["top_score"] == 85
    assert runs[0]["n_jobs_ranked"] == 2


def test_save_run_and_get_run():
    conn = _conn()
    cv_id = db.save_cv("cv", "cv.pdf", conn=conn)
    profile_id = db.save_profile(_profile(), cv_id, conn=conn)
    ranked = _ranked_jobs()
    result = _FakeRunResult(
        ranked_jobs=ranked,
        jobs_sources=["cache"],
        n_jobs_fetched=5,
        n_jobs_ranked=2,
        cost_usd=0.001,
        latency_s=2.0,
        failed=False,
    )
    db.save_run("run-2", profile_id, result, model="openai:gpt-4o-mini", conn=conn)

    run = db.get_run("run-2", conn=conn)
    assert run is not None
    assert run["model"] == "openai:gpt-4o-mini"
    assert run["jobs_sources"] == ["cache"]
    assert run["failed"] is False
    assert run["profile"].name == "Ada Lovelace"
    assert len(run["ranked_jobs"]) == 2
    # Sorted by score descending
    assert run["ranked_jobs"][0].fit_score == 85
    assert run["ranked_jobs"][1].fit_score == 62
    # Verify reconstructed job fields
    rj0 = run["ranked_jobs"][0]
    assert rj0.job.job_id == "j1"
    assert rj0.job.remote is True
    assert rj0.matched_skills == ["python", "pytorch"]
    assert rj0.gaps == ["kubernetes"]


def test_get_run_unknown_returns_none():
    conn = _conn()
    assert db.get_run("nonexistent", conn=conn) is None


def test_job_dedup_across_runs():
    conn = _conn()
    cv_id = db.save_cv("cv", "cv.pdf", conn=conn)
    profile_id = db.save_profile(_profile(), cv_id, conn=conn)
    ranked = _ranked_jobs()[:1]  # Just j1

    result1 = _FakeRunResult(ranked_jobs=ranked, n_jobs_ranked=1)
    db.save_run("run-a", profile_id, result1, model="m", conn=conn)

    # Second run with the same job
    result2 = _FakeRunResult(ranked_jobs=ranked, n_jobs_ranked=1)
    db.save_run("run-b", profile_id, result2, model="m", conn=conn)

    # One job_postings row, two run_jobs rows
    jp_count = conn.execute("SELECT COUNT(*) FROM job_postings WHERE job_id = 'j1'").fetchone()[0]
    rj_count = conn.execute("SELECT COUNT(*) FROM run_jobs WHERE job_id = 'j1'").fetchone()[0]
    assert jp_count == 1
    assert rj_count == 2


def test_init_db_idempotent():
    conn = _conn()
    # Call again — should not raise
    db.init_db(conn)
    db.init_db(conn)
    # Still works
    cv_id = db.save_cv("text", "f.pdf", conn=conn)
    assert cv_id


def test_list_runs_ordering():
    conn = _conn()
    cv_id = db.save_cv("cv", "cv.pdf", conn=conn)
    profile_id = db.save_profile(_profile(), cv_id, conn=conn)

    # Insert runs with explicit created_at to control order
    for i in range(3):
        conn.execute(
            "INSERT INTO runs (run_id, profile_id, reformulation_count, n_jobs_fetched, "
            "n_jobs_ranked, cost_usd, latency_s, failed, created_at) "
            "VALUES (?, ?, 0, 0, 0, 0, 0, 0, ?)",
            (f"run-{i}", profile_id, f"2025-01-0{i + 1}T00:00:00+00:00"),
        )
    conn.commit()

    runs = db.list_runs(conn=conn)
    assert len(runs) == 3
    # Newest first
    assert runs[0]["run_id"] == "run-2"
    assert runs[2]["run_id"] == "run-0"


def test_list_runs_pagination():
    conn = _conn()
    cv_id = db.save_cv("cv", "cv.pdf", conn=conn)
    profile_id = db.save_profile(_profile(), cv_id, conn=conn)

    for i in range(5):
        conn.execute(
            "INSERT INTO runs (run_id, profile_id, reformulation_count, n_jobs_fetched, "
            "n_jobs_ranked, cost_usd, latency_s, failed, created_at) "
            "VALUES (?, ?, 0, 0, 0, 0, 0, 0, ?)",
            (f"run-{i}", profile_id, f"2025-01-0{i + 1}T00:00:00+00:00"),
        )
    conn.commit()

    page1 = db.list_runs(limit=2, offset=0, conn=conn)
    page2 = db.list_runs(limit=2, offset=2, conn=conn)
    page3 = db.list_runs(limit=2, offset=4, conn=conn)
    assert len(page1) == 2
    assert len(page2) == 2
    assert len(page3) == 1
    # No overlapping IDs
    all_ids = [r["run_id"] for r in page1 + page2 + page3]
    assert len(set(all_ids)) == 5


def test_save_run_failed():
    conn = _conn()
    cv_id = db.save_cv("cv", "cv.pdf", conn=conn)
    profile_id = db.save_profile(_profile(), cv_id, conn=conn)
    result = _FakeRunResult(
        failed=True,
        error_message="API timeout",
        errors=["timeout after 30s"],
    )
    db.save_run("run-fail", profile_id, result, model="openai:gpt-4o-mini", conn=conn)

    run = db.get_run("run-fail", conn=conn)
    assert run is not None
    assert run["failed"] is True
    assert run["error_message"] == "API timeout"
    assert run["errors"] == ["timeout after 30s"]
    assert run["ranked_jobs"] == []


def test_save_profile_json_fields_survive():
    """Verify that JSON list fields roundtrip through the database."""
    conn = _conn()
    cv_id = db.save_cv("cv", "cv.pdf", conn=conn)
    profile = Profile(
        name="Test",
        primary_roles=["A", "B", "C"],
        skills=["x", "y"],
        locations=["City, Country"],
        languages=["en", "de"],
    )
    profile_id = db.save_profile(profile, cv_id, conn=conn)
    loaded = db.get_profile(profile_id, conn=conn)
    assert loaded is not None
    assert loaded.primary_roles == ["A", "B", "C"]
    assert loaded.skills == ["x", "y"]
    assert loaded.locations == ["City, Country"]
    assert loaded.languages == ["en", "de"]


def test_save_run_empty_ranked_jobs():
    conn = _conn()
    cv_id = db.save_cv("cv", "cv.pdf", conn=conn)
    profile_id = db.save_profile(_profile(), cv_id, conn=conn)
    result = _FakeRunResult(n_jobs_fetched=3, n_jobs_ranked=0)
    db.save_run("run-empty", profile_id, result, model="m", conn=conn)

    run = db.get_run("run-empty", conn=conn)
    assert run is not None
    assert run["ranked_jobs"] == []
    assert run["n_jobs_fetched"] == 3


def test_list_runs_empty():
    conn = _conn()
    runs = db.list_runs(conn=conn)
    assert runs == []
