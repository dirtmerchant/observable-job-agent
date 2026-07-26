"""SQLite persistence for CV/run history.

Stores CVs, profiles, runs, and ranked jobs so the History tab can browse past
searches across restarts. All writes are best-effort — the app works fine if the
database is unavailable.

The schema uses JSON text for list fields, INTEGER 0/1 for booleans, and ISO 8601
strings for timestamps.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from job_scout.config import get_settings
from job_scout.graph.schemas import JobPosting, Profile, RankedJob

logger = logging.getLogger(__name__)

_SCHEMA = """\
CREATE TABLE IF NOT EXISTS cvs (
    cv_id      TEXT PRIMARY KEY,
    filename   TEXT NOT NULL,
    cv_text    TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS profiles (
    profile_id       TEXT PRIMARY KEY,
    cv_id            TEXT NOT NULL REFERENCES cvs(cv_id),
    name             TEXT,
    seniority        TEXT,
    primary_roles    TEXT,
    skills           TEXT,
    years_experience REAL,
    locations        TEXT,
    languages        TEXT,
    remote_ok        INTEGER NOT NULL DEFAULT 0,
    raw_summary      TEXT,
    created_at       TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS runs (
    run_id               TEXT PRIMARY KEY,
    profile_id           TEXT NOT NULL REFERENCES profiles(profile_id),
    reformulation_count  INTEGER NOT NULL DEFAULT 0,
    n_jobs_fetched       INTEGER NOT NULL DEFAULT 0,
    n_jobs_ranked        INTEGER NOT NULL DEFAULT 0,
    jobs_sources         TEXT,
    cost_usd             REAL NOT NULL DEFAULT 0.0,
    latency_s            REAL NOT NULL DEFAULT 0.0,
    model                TEXT,
    opik_url             TEXT,
    failed               INTEGER NOT NULL DEFAULT 0,
    error_message        TEXT,
    errors               TEXT,
    created_at           TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS job_postings (
    job_id      TEXT NOT NULL,
    source      TEXT NOT NULL,
    title       TEXT NOT NULL,
    company     TEXT NOT NULL,
    location    TEXT NOT NULL,
    remote      INTEGER NOT NULL DEFAULT 0,
    description TEXT,
    url         TEXT,
    tags        TEXT,
    first_seen  TEXT NOT NULL,
    PRIMARY KEY (job_id, source)
);

CREATE TABLE IF NOT EXISTS run_jobs (
    run_id          TEXT NOT NULL REFERENCES runs(run_id),
    job_id          TEXT NOT NULL,
    source          TEXT NOT NULL,
    fit_score       INTEGER NOT NULL,
    fit_explanation TEXT,
    matched_skills  TEXT,
    gaps            TEXT,
    PRIMARY KEY (run_id, job_id, source),
    FOREIGN KEY (job_id, source) REFERENCES job_postings(job_id, source)
);

CREATE INDEX IF NOT EXISTS idx_runs_created ON runs(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_runs_profile ON runs(profile_id);
CREATE INDEX IF NOT EXISTS idx_profiles_cv ON profiles(cv_id);
"""

_conn_singleton: sqlite3.Connection | None = None


def init_db(conn: sqlite3.Connection) -> None:
    """Apply the schema idempotently. Safe to call multiple times."""
    conn.executescript(_SCHEMA)


def get_connection() -> sqlite3.Connection:
    """Return (or create) the module-level singleton connection.

    Creates the parent directory and database file if needed, enables WAL mode
    for better concurrent-read performance.
    """
    global _conn_singleton  # noqa: PLW0603
    if _conn_singleton is not None:
        return _conn_singleton

    db_path = get_settings().scout_db_path
    if db_path == ":memory:":
        conn = sqlite3.connect(":memory:")
    else:
        path = Path(db_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(path))
        conn.execute("PRAGMA journal_mode=WAL")

    conn.row_factory = sqlite3.Row
    init_db(conn)
    _conn_singleton = conn
    return conn


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _json_dumps(obj: object) -> str:
    return json.dumps(obj, ensure_ascii=False)


# ---------------------------------------------------------------------------
# CVs
# ---------------------------------------------------------------------------


def save_cv(cv_text: str, filename: str, *, conn: sqlite3.Connection | None = None) -> str:
    """Insert a CV record. Returns the generated cv_id."""
    conn = conn or get_connection()
    cv_id = uuid4().hex
    conn.execute(
        "INSERT INTO cvs (cv_id, filename, cv_text, created_at) VALUES (?, ?, ?, ?)",
        (cv_id, filename, cv_text, _now()),
    )
    conn.commit()
    return cv_id


# ---------------------------------------------------------------------------
# Profiles
# ---------------------------------------------------------------------------


def save_profile(profile: Profile, cv_id: str, *, conn: sqlite3.Connection | None = None) -> str:
    """Insert a profile linked to a CV. Returns the generated profile_id."""
    conn = conn or get_connection()
    profile_id = uuid4().hex
    conn.execute(
        "INSERT INTO profiles "
        "(profile_id, cv_id, name, seniority, primary_roles, skills, "
        "years_experience, locations, languages, remote_ok, raw_summary, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            profile_id,
            cv_id,
            profile.name,
            profile.seniority,
            _json_dumps(profile.primary_roles),
            _json_dumps(profile.skills),
            profile.years_experience,
            _json_dumps(profile.locations),
            _json_dumps(profile.languages),
            int(profile.remote_ok),
            profile.raw_summary,
            _now(),
        ),
    )
    conn.commit()
    return profile_id


def get_profile(profile_id: str, *, conn: sqlite3.Connection | None = None) -> Profile | None:
    """Load a profile by ID, or return None if not found."""
    conn = conn or get_connection()
    row = conn.execute("SELECT * FROM profiles WHERE profile_id = ?", (profile_id,)).fetchone()
    if row is None:
        return None
    return _row_to_profile(row)


def _row_to_profile(row: sqlite3.Row) -> Profile:
    return Profile(
        name=row["name"],
        seniority=row["seniority"] or "unknown",
        primary_roles=json.loads(row["primary_roles"] or "[]"),
        skills=json.loads(row["skills"] or "[]"),
        years_experience=row["years_experience"],
        locations=json.loads(row["locations"] or "[]"),
        languages=json.loads(row["languages"] or "[]"),
        remote_ok=bool(row["remote_ok"]),
        raw_summary=row["raw_summary"] or "",
    )


# ---------------------------------------------------------------------------
# Runs
# ---------------------------------------------------------------------------


def save_run(
    run_id: str,
    profile_id: str,
    result: object,
    model: str,
    *,
    conn: sqlite3.Connection | None = None,
) -> None:
    """Persist a completed run, its job postings, and per-run scores.

    ``result`` is expected to be a ``RunResult`` (imported lazily to avoid
    circular imports). All inserts happen in one transaction.
    """
    conn = conn or get_connection()
    now = _now()

    conn.execute("BEGIN")
    try:
        conn.execute(
            "INSERT INTO runs "
            "(run_id, profile_id, reformulation_count, n_jobs_fetched, n_jobs_ranked, "
            "jobs_sources, cost_usd, latency_s, model, opik_url, failed, error_message, errors, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                run_id,
                profile_id,
                getattr(result, "reformulation_count", 0),
                getattr(result, "n_jobs_fetched", 0),
                getattr(result, "n_jobs_ranked", 0),
                _json_dumps(getattr(result, "jobs_sources", [])),
                getattr(result, "cost_usd", 0.0),
                getattr(result, "latency_s", 0.0),
                model,
                getattr(result, "opik_url", ""),
                int(getattr(result, "failed", False)),
                getattr(result, "error_message", ""),
                _json_dumps(getattr(result, "errors", [])),
                now,
            ),
        )

        ranked_jobs: list[RankedJob] = getattr(result, "ranked_jobs", [])
        for rj in ranked_jobs:
            job = rj.job
            # Upsert the job posting (same job may appear in multiple runs)
            conn.execute(
                "INSERT OR IGNORE INTO job_postings "
                "(job_id, source, title, company, location, remote, description, url, tags, first_seen) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    job.job_id,
                    job.source,
                    job.title,
                    job.company,
                    job.location,
                    int(job.remote),
                    job.description,
                    job.url,
                    _json_dumps(job.tags),
                    now,
                ),
            )
            conn.execute(
                "INSERT INTO run_jobs "
                "(run_id, job_id, source, fit_score, fit_explanation, matched_skills, gaps) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    run_id,
                    job.job_id,
                    job.source,
                    rj.fit_score,
                    rj.fit_explanation,
                    _json_dumps(rj.matched_skills),
                    _json_dumps(rj.gaps),
                ),
            )
        conn.commit()
    except Exception:
        conn.rollback()
        raise


def list_runs(*, limit: int = 20, offset: int = 0, conn: sqlite3.Connection | None = None) -> list[dict]:
    """Return recent runs with profile name and top score, newest first."""
    conn = conn or get_connection()
    rows = conn.execute(
        "SELECT r.run_id, r.created_at, r.model, r.cost_usd, r.latency_s, "
        "r.n_jobs_ranked, r.failed, p.name AS profile_name, "
        "(SELECT MAX(rj.fit_score) FROM run_jobs rj WHERE rj.run_id = r.run_id) AS top_score "
        "FROM runs r JOIN profiles p ON r.profile_id = p.profile_id "
        "ORDER BY r.created_at DESC LIMIT ? OFFSET ?",
        (limit, offset),
    ).fetchall()
    return [dict(row) for row in rows]


def get_run(run_id: str, *, conn: sqlite3.Connection | None = None) -> dict | None:
    """Load full run detail: metadata, profile, and reconstructed RankedJob list."""
    conn = conn or get_connection()
    run_row = conn.execute("SELECT * FROM runs WHERE run_id = ?", (run_id,)).fetchone()
    if run_row is None:
        return None

    run = dict(run_row)
    run["jobs_sources"] = json.loads(run["jobs_sources"] or "[]")
    run["errors"] = json.loads(run["errors"] or "[]")
    run["failed"] = bool(run["failed"])

    # Load profile
    profile_row = conn.execute("SELECT * FROM profiles WHERE profile_id = ?", (run["profile_id"],)).fetchone()
    run["profile"] = _row_to_profile(profile_row) if profile_row else None

    # Load ranked jobs
    job_rows = conn.execute(
        "SELECT rj.*, jp.title, jp.company, jp.location, jp.remote, "
        "jp.description, jp.url, jp.tags "
        "FROM run_jobs rj "
        "JOIN job_postings jp ON rj.job_id = jp.job_id AND rj.source = jp.source "
        "WHERE rj.run_id = ? ORDER BY rj.fit_score DESC",
        (run_id,),
    ).fetchall()

    ranked_jobs = []
    for jr in job_rows:
        job = JobPosting(
            job_id=jr["job_id"],
            source=jr["source"],
            title=jr["title"],
            company=jr["company"],
            location=jr["location"],
            remote=bool(jr["remote"]),
            description=jr["description"] or "",
            url=jr["url"] or "",
            tags=json.loads(jr["tags"] or "[]"),
        )
        ranked_jobs.append(
            RankedJob(
                job=job,
                fit_score=jr["fit_score"],
                fit_explanation=jr["fit_explanation"] or "",
                matched_skills=json.loads(jr["matched_skills"] or "[]"),
                gaps=json.loads(jr["gaps"] or "[]"),
            )
        )
    run["ranked_jobs"] = ranked_jobs
    return run
