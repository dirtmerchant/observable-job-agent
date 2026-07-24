"""Score each fetched job against the profile, one LLM call per batch.

The LLM returns lean ``JobScore`` objects keyed by ``job_id``; we pair each back
to its ``JobPosting`` to build a ``RankedJob``.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator

from langchain_core.exceptions import OutputParserException
from pydantic import ValidationError

from job_scout.config import get_settings
from job_scout.graph.prompts.rank_jobs import RANK_JOBS_PROMPT
from job_scout.graph.schemas import JobPosting, JobScores, Profile, RankedJob
from job_scout.graph.state import AgentState
from job_scout.llm import ensure_budget, get_chat_model

logger = logging.getLogger(__name__)

BATCH_SIZE = 5


def _render_profile(profile: Profile) -> str:
    """Format the profile as plain text for the ranking prompt."""
    return (
        f"Name: {profile.name}\n"
        f"Seniority: {profile.seniority}\n"
        f"Roles: {', '.join(profile.primary_roles)}\n"
        f"Skills: {', '.join(profile.skills)}\n"
        f"Years experience: {profile.years_experience}\n"
        f"Locations: {', '.join(profile.locations)}\n"
        f"Remote ok: {profile.remote_ok}"
    )


def _render_jobs(jobs: list[JobPosting]) -> str:
    """Format a batch of jobs as plain text for the ranking prompt."""
    return "\n\n---\n\n".join(
        f"job_id: {job.job_id}\n"
        f"title: {job.title}\n"
        f"company: {job.company}\n"
        f"location: {job.location} (remote: {job.remote})\n"
        f"description: {job.description[:1500]}"
        for job in jobs
    )


def _batches(items: list[JobPosting], size: int) -> Iterator[list[JobPosting]]:
    """Yield ``items`` in chunks of ``size``."""
    for i in range(0, len(items), size):
        yield items[i : i + size]


def rank_jobs(state: AgentState) -> dict:
    """Score each fetched job against the profile and return them sorted by fit."""
    settings = get_settings()
    profile = state["profile"]
    jobs = state.get("jobs", [])
    if not jobs:
        return {"ranked_jobs": []}

    by_id = {job.job_id: job for job in jobs}
    calls = state.get("llm_calls", 0)
    n_batches = (len(jobs) + BATCH_SIZE - 1) // BATCH_SIZE
    ensure_budget(calls, n_batches, settings.max_llm_calls_per_run)

    model = get_chat_model(settings.scout_model, temperature=0.0).with_structured_output(JobScores)
    ranked: list[RankedJob] = []
    errors: list[str] = list(state.get("errors") or [])
    for batch in _batches(jobs, BATCH_SIZE):
        prompt = RANK_JOBS_PROMPT.format(profile=_render_profile(profile), jobs=_render_jobs(batch))
        calls += 1
        try:
            result: JobScores = model.invoke(prompt)
        except (OutputParserException, ValidationError) as exc:
            batch_ids = [j.job_id for j in batch]
            msg = f"rank_jobs: structured-output parse failed for batch {batch_ids}: {exc}"
            logger.warning(msg)
            errors.append(msg)
            continue
        for score in result.scores:
            job = by_id.get(score.job_id)
            if job is None:
                continue
            ranked.append(
                RankedJob(
                    job=job,
                    fit_score=score.fit_score,
                    fit_explanation=score.fit_explanation,
                    matched_skills=score.matched_skills,
                    gaps=score.gaps,
                )
            )

    ranked.sort(key=lambda r: r.fit_score, reverse=True)
    return {"ranked_jobs": ranked, "llm_calls": calls, "errors": errors}
