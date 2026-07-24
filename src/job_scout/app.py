"""Gradio UI for Job Scout.

A three-step wizard with a refined editorial look: warm-paper background with a
faint gradient-and-grain wash, a display serif (Fraunces) paired with a mono for
data (IBM Plex Mono), emerald accent. The flow is (1) drop your resume,
(2) review the extracted profile, (3) find jobs — ranked as cards with a
conic-gauge fit score, matched-skill chips, and honest gaps.
"""

from __future__ import annotations

from html import escape
from uuid import uuid4

import gradio as gr

from job_scout.graph.schemas import Profile, RankedJob
from job_scout.profile import extract_profile
from job_scout.runner import RunResult, stream_search
from job_scout.tools.cv_reader import CVReadError, extract_cv_text
from job_scout.tracing import opik_url, register_prompts

CAPTION = "Prepares applications — never submits them."

INTRO_HTML = """
<p class="js-lead">Drop your resume and let Job Scout find roles that actually match it —
ranked by fit, with the gaps shown honestly.</p>
<ol class="js-steps">
  <li><span class="js-num">1</span><div><b>Drop your resume</b> (PDF) below.</div></li>
  <li><span class="js-num">2</span><div>We <b>read it</b> and show your skills, experience, and locations.</div></li>
  <li><span class="js-num">3</span><div>Hit <b>Find jobs</b> to get openings ranked by fit.</div></li>
</ol>
"""

THEME = gr.themes.Soft(
    primary_hue=gr.themes.colors.emerald,
    secondary_hue=gr.themes.colors.stone,
    neutral_hue=gr.themes.colors.stone,
    font=(gr.themes.GoogleFont("Public Sans"), "ui-sans-serif", "system-ui", "sans-serif"),
    radius_size=gr.themes.sizes.radius_md,
).set(
    body_background_fill="#FBFAF8",
    body_background_fill_dark="#141311",
    block_background_fill="#FFFFFF",
    block_background_fill_dark="#1E1D1A",
    button_primary_background_fill="#0E6E4A",
    button_primary_background_fill_hover="#0A5539",
    button_primary_text_color="#FFFFFF",
    button_large_radius="12px",
)

# Fine tiled grain, kept as a constant so the CSS block stays within line length.
_GRAIN = "url(\"data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='140' height='140'%3E%3Cfilter id='n'%3E%3CfeTurbulence type='fractalNoise' baseFrequency='0.85' numOctaves='2'/%3E%3C/filter%3E%3Crect width='100%25' height='100%25' filter='url(%23n)' opacity='0.5'/%3E%3C/svg%3E\")"  # noqa: E501

CSS = """
@import url('https://fonts.googleapis.com/css2?family=Fraunces:opsz,wght@9..144,400;9..144,500;9..144,600;9..144,700&family=Public+Sans:wght@400;500;600&family=IBM+Plex+Mono:wght@500;600&display=swap');

:root {
  --js-accent: #0E6E4A;
  --js-accent-bright: #0E9C68;
  --js-ring-track: rgba(20,19,17,0.08);
}
.dark {
  --js-ring-track: rgba(255,255,255,0.10);
}

/* --- Atmospheric background: faint emerald + amber wash over warm paper --- */
.gradio-container {
  max-width: 760px !important;
  margin: 0 auto !important;
  position: relative;
}
body::before {
  content: "";
  position: fixed; inset: 0; z-index: 0; pointer-events: none;
  background:
    radial-gradient(60% 45% at 12% 4%, rgba(14,156,104,0.10), transparent 70%),
    radial-gradient(55% 45% at 92% 8%, rgba(180,83,9,0.07), transparent 72%);
}
.dark body::before {
  background:
    radial-gradient(60% 45% at 12% 4%, rgba(14,156,104,0.14), transparent 70%),
    radial-gradient(55% 45% at 92% 8%, rgba(180,83,9,0.10), transparent 72%);
}
/* fine grain */
body::after {
  content: "";
  position: fixed; inset: 0; z-index: 0; pointer-events: none; opacity: 0.35;
  background-image: __GRAIN__;
}
.dark body::after { opacity: 0.5; mix-blend-mode: screen; }
.gradio-container > * { position: relative; z-index: 1; }

/* --- Header --- */
#js-header { text-align: center; padding: 22px 0 4px; }
#js-header .js-mark {
  display: inline-flex; align-items: center; justify-content: center; gap: 10px;
}
#js-header .js-mark svg { width: 30px; height: 30px; color: var(--js-accent); }
#js-header h1 {
  font-family: 'Fraunces', Georgia, serif; font-weight: 600;
  font-size: 2.85rem; letter-spacing: -0.025em; margin: 0; line-height: 1.02;
}
#js-header .js-tag {
  display: inline-block; margin-top: 13px; font-size: 0.76rem; letter-spacing: 0.01em;
  color: var(--body-text-color-subdued);
  border: 1px solid var(--border-color-primary); border-radius: 999px; padding: 4px 14px;
  background: color-mix(in srgb, var(--block-background-fill) 55%, transparent);
  backdrop-filter: blur(4px);
}

/* --- Stepper --- */
.js-stepper {
  list-style: none; display: flex; align-items: center; justify-content: center;
  gap: 0; margin: 20px auto 6px; padding: 0; max-width: 420px;
}
.js-step { display: flex; align-items: center; gap: 9px; font-size: 0.82rem; }
.js-step-dot {
  width: 26px; height: 26px; border-radius: 50%; flex: none;
  display: grid; place-items: center; font-size: 0.78rem; font-weight: 700;
  font-family: 'IBM Plex Mono', monospace;
  border: 1.5px solid var(--border-color-primary); color: var(--body-text-color-subdued);
  background: var(--block-background-fill); transition: all .2s ease;
}
.js-step-label { color: var(--body-text-color-subdued); font-weight: 500; }
.js-step::after {
  content: ""; width: 34px; height: 1.5px; margin: 0 12px;
  background: var(--border-color-primary); border-radius: 2px;
}
.js-step:last-child::after { display: none; }
.js-step-active .js-step-dot {
  border-color: var(--js-accent); color: #fff; background: var(--js-accent);
  box-shadow: 0 0 0 4px rgba(14,110,74,0.14);
}
.js-step-active .js-step-label { color: var(--body-text-color); font-weight: 600; }
.js-step-done .js-step-dot {
  border-color: var(--js-accent); color: var(--js-accent);
  background: rgba(14,110,74,0.12);
}

/* --- Intro --- */
.js-lead { text-align: center; font-size: 1.05rem; line-height: 1.55; color: var(--body-text-color-subdued);
  margin: 14px auto 4px; max-width: 480px; }
.js-steps { list-style: none; padding: 0; margin: 18px auto 8px; max-width: 470px; }
.js-steps li { display: flex; gap: 13px; align-items: flex-start; padding: 9px 0; font-size: 0.95rem; line-height: 1.45; }
.js-steps .js-num { flex: none; width: 26px; height: 26px; border-radius: 50%;
  background: rgba(14,110,74,0.12); color: var(--js-accent); font-weight: 700; font-size: 0.82rem;
  font-family: 'IBM Plex Mono', monospace; margin-top: 1px;
  display: flex; align-items: center; justify-content: center; }

.js-section-label { font-size: 0.76rem; font-weight: 600; letter-spacing: 0.08em;
  text-transform: uppercase; color: var(--body-text-color-subdued); margin: 4px 0 10px 2px; }
.js-muted { color: var(--body-text-color-subdued); }

/* --- Dropzone polish --- */
.js-drop { border: 1.5px dashed var(--border-color-primary) !important;
  border-radius: 16px !important; background: color-mix(in srgb, var(--block-background-fill) 70%, transparent) !important;
  transition: border-color .2s ease, background .2s ease; }
.js-drop:hover { border-color: var(--js-accent) !important; }

/* --- Cards --- */
.js-card { background: var(--block-background-fill); border: 1px solid var(--border-color-primary);
  border-radius: 16px; padding: 20px 22px; box-shadow: 0 1px 2px rgba(20,19,17,0.03); }
.js-profile-name { font-family: 'Fraunces', serif; font-size: 1.5rem; font-weight: 600; margin: 0; letter-spacing: -0.01em; }
.js-profile-sub { color: var(--body-text-color-subdued); font-size: 0.92rem; margin: 3px 0 14px; }
.js-profile-grid { display: grid; grid-template-columns: repeat(3, 1fr); gap: 12px; margin: 4px 0 14px;
  border-top: 1px solid var(--border-color-primary); padding-top: 14px; }
.js-stat .js-stat-val { font-family: 'IBM Plex Mono', monospace; font-size: 1.15rem; font-weight: 600; line-height: 1; }
.js-stat .js-stat-key { font-size: 0.72rem; text-transform: uppercase; letter-spacing: 0.05em;
  color: var(--body-text-color-subdued); margin-top: 5px; }
.js-profile-row { font-size: 0.9rem; margin: 3px 0; }
.js-profile-row b { font-weight: 600; }
.js-pill { display: inline-block; font-size: 0.75rem; padding: 3px 11px; border-radius: 999px;
  background: rgba(14,110,74,0.10); color: var(--js-accent); margin: 5px 5px 0 0; }

/* --- Job cards --- */
.js-jobs { display: flex; flex-direction: column; gap: 12px; }
.js-job { border: 1px solid var(--border-color-primary); border-radius: 14px;
  padding: 17px 19px; background: var(--block-background-fill);
  box-shadow: 0 1px 2px rgba(20,19,17,0.03);
  transition: box-shadow .18s ease, transform .18s ease, border-color .18s ease;
  opacity: 0; transform: translateY(8px); animation: js-rise .4s ease forwards; }
.js-job:hover { box-shadow: 0 12px 30px rgba(20,19,17,0.09); transform: translateY(-2px);
  border-color: color-mix(in srgb, var(--js-accent) 40%, var(--border-color-primary)); }
@keyframes js-rise { to { opacity: 1; transform: none; } }

.js-job-head { display: flex; justify-content: space-between; align-items: flex-start; gap: 16px; }
.js-job-title { font-weight: 600; font-size: 1.06rem; text-decoration: none; color: var(--body-text-color);
  line-height: 1.3; }
.js-job-title:hover { color: var(--js-accent); }
.js-job-meta { font-size: 0.85rem; color: var(--body-text-color-subdued); margin-top: 4px;
  display: flex; align-items: center; flex-wrap: wrap; gap: 7px; }
.js-remote, .js-source { font-size: 0.66rem; font-weight: 600; text-transform: uppercase; letter-spacing: 0.04em;
  border-radius: 5px; padding: 2px 7px; line-height: 1.5; }
.js-remote { color: var(--js-accent); border: 1px solid rgba(14,110,74,0.35); }
.js-source { color: var(--body-text-color-subdued); border: 1px solid var(--border-color-primary); }
.js-job-why { font-size: 0.92rem; line-height: 1.55; margin: 12px 0 0; }

/* chips */
.js-chips { margin-top: 11px; display: flex; flex-wrap: wrap; gap: 6px; }
.js-chip { display: inline-flex; align-items: center; gap: 4px; font-size: 0.75rem;
  padding: 3px 10px; border-radius: 999px; line-height: 1.5; }
.js-chip-match { background: rgba(14,110,74,0.11); color: var(--js-accent); }
.js-chip-gap { background: rgba(100,116,139,0.13); color: var(--body-text-color-subdued); }

/* --- Fit gauge --- */
.js-fit-ring { --size: 56px; width: var(--size); height: var(--size); flex: none;
  border-radius: 50%; display: grid; place-items: center; position: relative;
  background: conic-gradient(var(--ring) calc(var(--val) * 1%), var(--js-ring-track) 0); }
.js-fit-ring::before { content: ""; position: absolute; inset: 5px; border-radius: 50%;
  background: var(--block-background-fill); }
.js-fit-num { position: relative; font-family: 'IBM Plex Mono', monospace; font-weight: 600;
  font-size: 1.05rem; color: var(--tier-fg); }
.js-fit-high { --ring: var(--js-accent-bright); --tier-fg: #0E6E4A; }
.js-fit-mid  { --ring: #D97706; --tier-fg: #B45309; }
.js-fit-low  { --ring: #94A3B8; --tier-fg: #64748B; }
.dark .js-fit-high { --tier-fg: #34D399; }
.dark .js-fit-mid  { --tier-fg: #FBBF24; }
.dark .js-fit-low  { --tier-fg: #94A3B8; }

/* --- States --- */
.js-empty { text-align: center; padding: 40px 20px; color: var(--body-text-color-subdued); }
.js-empty .js-empty-icon { font-size: 1.8rem; opacity: 0.8; margin-bottom: 8px; }
.js-status { font-size: 0.86rem; color: var(--body-text-color-subdued); text-align: center; min-height: 18px; }
.js-status-err { color: #B45309; }
.js-spin { display: inline-block; width: 13px; height: 13px; margin-right: 8px; vertical-align: -1px;
  border: 2px solid rgba(14,110,74,0.25); border-top-color: var(--js-accent); border-radius: 50%;
  animation: js-rotate .7s linear infinite; }
@keyframes js-rotate { to { transform: rotate(360deg); } }

/* --- Footer --- */
.js-footer { text-align: center; font-size: 0.82rem; color: var(--body-text-color-subdued);
  border-top: 1px solid var(--border-color-primary); margin-top: 20px; padding-top: 15px; }
.js-footer .js-meta-mono { font-family: 'IBM Plex Mono', monospace; }
.js-footer a { color: var(--js-accent); text-decoration: none; }
.js-footer a:hover { text-decoration: underline; }

/* accessibility: visible focus */
a:focus-visible, button:focus-visible, .js-job-title:focus-visible {
  outline: 2px solid var(--js-accent); outline-offset: 2px; border-radius: 4px; }

@media (max-width: 520px) {
  #js-header h1 { font-size: 2.3rem; }
  .js-step-label { display: none; }
  .js-step::after { width: 22px; margin: 0 8px; }
  .js-profile-grid { grid-template-columns: repeat(3, 1fr); gap: 8px; }
}
@media (prefers-reduced-motion: reduce) {
  .js-job { animation: none; opacity: 1; transform: none; }
  .js-spin { animation: none; }
}
""".replace("__GRAIN__", _GRAIN)

# Small compass mark for the wordmark.
_MARK = (
    '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" '
    'stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">'
    '<circle cx="12" cy="12" r="9"/><path d="m15.5 8.5-2.1 5-5 2.1 2.1-5z"/></svg>'
)


def _stepper(active: int) -> str:
    """Render the 3-step progress indicator with ``active`` (1-3) highlighted."""
    labels = ("Resume", "Profile", "Jobs")
    items = []
    for i, label in enumerate(labels, 1):
        state = "done" if i < active else "active" if i == active else "todo"
        mark = "✓" if state == "done" else str(i)
        items.append(
            f'<li class="js-step js-step-{state}">'
            f'<span class="js-step-dot">{mark}</span>'
            f'<span class="js-step-label">{label}</span></li>'
        )
    return f'<ol class="js-stepper">{"".join(items)}</ol>'


def _loading_html(text: str) -> str:
    """Centered spinner with a message."""
    return f'<div class="js-empty"><div class="js-empty-icon"><span class="js-spin"></span></div><div>{escape(text)}</div></div>'


def _chips(items: list[str], kind: str, limit: int) -> str:
    """Render a row of matched-skill or gap chips."""
    icon = "✓ " if kind == "match" else ""
    shown = "".join(f'<span class="js-chip js-chip-{kind}">{icon}{escape(s)}</span>' for s in items[:limit])
    return f'<div class="js-chips">{shown}</div>' if shown else ""


def _profile_html(profile: Profile | None) -> str:
    """Render the extracted profile as a card."""
    if profile is None:
        return '<div class="js-card js-muted">Could not read a profile from that resume.</div>'
    skills = "".join(f'<span class="js-pill">{escape(s)}</span>' for s in profile.skills) or "—"
    years = f"{profile.years_experience:g}" if profile.years_experience else "—"
    languages = escape(", ".join(profile.languages) or "—")
    return (
        '<div class="js-card">'
        f'<p class="js-profile-name">{escape(profile.name or "Candidate")}</p>'
        f'<p class="js-profile-sub">{escape(profile.seniority.title())} · '
        f"{escape(', '.join(profile.primary_roles) or '—')}</p>"
        '<div class="js-profile-grid">'
        f'<div class="js-stat"><div class="js-stat-val">{years}</div><div class="js-stat-key">Years exp</div></div>'
        f'<div class="js-stat"><div class="js-stat-val">{"Yes" if profile.remote_ok else "No"}</div>'
        '<div class="js-stat-key">Remote</div></div>'
        f'<div class="js-stat"><div class="js-stat-val">{len(profile.skills)}</div><div class="js-stat-key">Skills</div></div>'
        "</div>"
        f'<p class="js-profile-row"><b>Locations</b> &nbsp;{escape(", ".join(profile.locations) or "—")}</p>'
        f'<p class="js-profile-row"><b>Languages</b> &nbsp;{languages}</p>'
        f'<div style="margin-top:12px">{skills}</div>'
        "</div>"
    )


def _fit_class(score: int) -> str:
    """Return the CSS tier class for a fit score."""
    if score >= 80:
        return "js-fit-high"
    if score >= 60:
        return "js-fit-mid"
    return "js-fit-low"


def _fit_ring(score: int) -> str:
    """Render the fit score as a conic-gradient gauge."""
    return (
        f'<div class="js-fit-ring {_fit_class(score)}" style="--val:{score}" '
        f'role="img" aria-label="Fit score {score} out of 100">'
        f'<span class="js-fit-num">{score}</span></div>'
    )


def _job_card(ranked: RankedJob, index: int) -> str:
    """Render one ranked job as a card with a conic fit gauge and skill chips."""
    job = ranked.job
    title = escape(job.title)
    if job.url:
        title_html = f'<a class="js-job-title" href="{escape(job.url)}" target="_blank" rel="noopener">{title}</a>'
    else:
        title_html = f'<span class="js-job-title">{title}</span>'
    remote = '<span class="js-remote">Remote</span>' if job.remote else ""
    source = f'<span class="js-source">{escape(job.source)}</span>'
    matched = _chips(ranked.matched_skills, "match", 6)
    gaps = _chips(ranked.gaps, "gap", 4)
    delay = f"animation-delay:{min(index, 8) * 55}ms"
    return (
        f'<div class="js-job" style="{delay}">'
        '<div class="js-job-head">'
        f"<div>{title_html}"
        f'<div class="js-job-meta">{escape(job.company)} · {escape(job.location)}{remote}{source}</div></div>'
        f"{_fit_ring(ranked.fit_score)}"
        "</div>"
        f'<p class="js-job-why">{escape(ranked.fit_explanation)}</p>'
        f"{matched}{gaps}"
        "</div>"
    )


def _results_html(result: RunResult) -> str:
    """Render the ranked jobs as cards, or an empty state."""
    if not result.ranked_jobs:
        return (
            '<div class="js-empty"><div class="js-empty-icon">🔍</div>'
            "<div>No matching jobs found. Try a resume with more detail, or check back later.</div></div>"
        )
    cards = "".join(_job_card(r, i) for i, r in enumerate(result.ranked_jobs))
    return f'<div class="js-jobs">{cards}</div>'


def _footer_html(result: RunResult) -> str:
    """Render the run footer: cost, latency, job source, and the Opik link."""
    link = f'<a href="{result.opik_url or opik_url()}" target="_blank" rel="noopener">view traces in Opik ↗</a>'
    if result.failed:
        body = f"⚠ run failed — {escape(result.error_message)}. The trace has details · {link}"
    else:
        sources = escape(", ".join(result.jobs_sources) or "none")
        sep = ' <span class="js-muted">·</span> '
        body = (
            f'<span class="js-meta-mono">${result.cost_usd:.4f}</span>{sep}'
            f'<span class="js-meta-mono">{result.latency_s}s</span>{sep}'
            f"source: {sources}{sep}{link}"
        )
    return f'<div class="js-footer">{body}</div>'


def _status(text: str, error: bool = False) -> str:
    """Render a status line on the start page."""
    cls = "js-status js-status-err" if error else "js-status"
    return f'<div class="{cls}">{escape(text)}</div>'


def on_upload(file_path: str | None, thread_id: str):
    """Step 1 → 2: read the resume, extract the profile, and reveal the profile page.

    Outputs: (page_start, page_profile, profile_html, cv_text_state, start_status, profile_state).
    """
    stay = gr.update(visible=True), gr.update(visible=False)
    go = gr.update(visible=False), gr.update(visible=True)

    if not file_path:
        yield (*stay, gr.update(), "", _status("Please drop a PDF resume.", error=True), None)
        return
    try:
        cv_text = extract_cv_text(file_path)
    except CVReadError as exc:
        yield (*stay, gr.update(), "", _status(f"Could not read that PDF: {exc}", error=True), None)
        return

    yield (*go, _loading_html("Reading your resume…"), cv_text, "", gr.update())
    try:
        profile = extract_profile(cv_text, thread_id=thread_id, tags=["phase-1", "ui", "extract"])
    except Exception as exc:  # noqa: BLE001 - show a friendly error and return to start
        yield (*stay, gr.update(), "", _status(f"Couldn't read a profile: {exc}", error=True), None)
        return
    yield (*go, _profile_html(profile), cv_text, "", profile)


def on_find(cv_text: str, profile: Profile | None, thread_id: str):
    """Step 2 → 3: run the job-finding graph for the extracted profile and stream results.

    Outputs: (page_profile, page_results, results_html, footer_html).
    """
    go = gr.update(visible=False), gr.update(visible=True)
    if profile is None:
        yield (gr.update(visible=True), gr.update(visible=False), gr.update(), "")
        return

    yield (*go, _loading_html("Searching for jobs…"), "")
    result = RunResult()
    for kind, payload in stream_search(profile, cv_text=cv_text, thread_id=thread_id, tags=["phase-1", "ui"]):
        if kind == "status":
            yield (*go, _loading_html(str(payload)), "")
        elif kind == "result":
            result = payload  # type: ignore[assignment]

    yield (*go, _results_html(result), _footer_html(result))


def reset():
    """Return to step 1 and clear the wizard.

    Outputs: (page_start, page_profile, page_results, cv_file, cv_text_state,
    start_status, results_html, footer_html).
    """
    return (
        gr.update(visible=True),
        gr.update(visible=False),
        gr.update(visible=False),
        None,
        "",
        "",
        "",
        "",
    )


def build_app() -> gr.Blocks:
    """Build the three-step wizard app."""
    register_prompts()

    with gr.Blocks(title="Job Scout", theme=THEME, css=CSS) as demo:
        thread_id = gr.State(lambda: str(uuid4()))
        cv_text_state = gr.State("")
        profile_state = gr.State(None)

        gr.HTML(
            f'<div id="js-header"><div class="js-mark">{_MARK}<h1>Job Scout</h1></div>'
            f'<div><span class="js-tag">{CAPTION}</span></div></div>'
        )

        with gr.Group(visible=True) as page_start:
            gr.HTML(_stepper(1))
            gr.HTML(INTRO_HTML)
            cv_file = gr.File(label="", file_types=[".pdf"], type="filepath", height=150, elem_classes=["js-drop"])
            start_status = gr.HTML('<div class="js-status"></div>')

        with gr.Group(visible=False) as page_profile:
            gr.HTML(_stepper(2))
            gr.HTML('<p class="js-section-label">Your profile</p>')
            profile_out = gr.HTML()
            find_btn = gr.Button("Find jobs", variant="primary", size="lg")
            change_btn = gr.Button("Upload a different resume", variant="secondary")

        with gr.Group(visible=False) as page_results:
            gr.HTML(_stepper(3))
            gr.HTML('<p class="js-section-label">Ranked jobs</p>')
            results_out = gr.HTML()
            footer_out = gr.HTML()
            restart_btn = gr.Button("Start over", variant="secondary")

        cv_file.upload(
            on_upload,
            inputs=[cv_file, thread_id],
            outputs=[page_start, page_profile, profile_out, cv_text_state, start_status, profile_state],
        )
        find_btn.click(
            on_find,
            inputs=[cv_text_state, profile_state, thread_id],
            outputs=[page_profile, page_results, results_out, footer_out],
        )
        reset_outputs = [page_start, page_profile, page_results, cv_file, cv_text_state, start_status, results_out, footer_out]
        change_btn.click(reset, outputs=reset_outputs)
        restart_btn.click(reset, outputs=reset_outputs)

    return demo


def main() -> None:
    """Launch the Gradio app."""
    app = build_app()
    # Mount a lightweight health endpoint for k8s probes.
    from fastapi import FastAPI
    from fastapi.responses import JSONResponse

    fastapi_app: FastAPI = app.app  # type: ignore[union-attr]

    @fastapi_app.get("/healthz")
    def healthz() -> JSONResponse:
        return JSONResponse({"status": "ok"})

    app.launch()


if __name__ == "__main__":
    main()
