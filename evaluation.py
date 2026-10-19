"""
Evaluation metrics for the Job Application Assistant.

These are deterministic, no-extra-API quality checks computed on the live outputs the
app already produced, so the Evaluation tab can show how well the AI is performing:

- Groundedness / Faithfulness : is the resume built only from real profile data?
                                (anti-hallucination — the core RAG guardrail)
- ATS keyword coverage        : do the JD's required skills appear in the resume?
- Skill-match coverage        : share of required skills the candidate already has
- Guardrail compliance        : a pass/fail checklist of the hard AI guardrails

Performance metrics (latency / tokens / cost) come from llm_provider.get_metrics().
"""

import re

INTERNAL_FLAG_PATTERNS = [
    r"\bdowngrad", r"\bweak\b", r"\bstrong match\b", r"\bpartial match\b",
    r"\bmissing skill", r"\bconfidence\b", r"\bscore\b", r"level\s*:", r"\bflag\b",
    r"current_profile_state", r"not available",
]


def _lower(x):
    return ("" if x is None else str(x)).lower()


def _resume_skill_terms(resume):
    terms = []
    for g in resume.get("technical_skills", []) or []:
        if isinstance(g, dict):
            terms += [str(i) for i in g.get("items", [])]
    terms += [str(s) for s in resume.get("skills", []) or []]
    return [t.strip() for t in terms if t and str(t).strip()]


def _profile_known_text(profile_state, profile_raw_text=""):
    bits = [profile_raw_text or ""]
    for s in profile_state.get("skills", []):
        bits.append(s.get("name", "") if isinstance(s, dict) else str(s))
    bits += [str(t) for t in profile_state.get("tools", [])]
    for e in profile_state.get("experience", []):
        if isinstance(e, dict):
            bits += [e.get("company", ""), e.get("role", ""), e.get("title", "")]
            for p in e.get("positions", []) or []:
                bits.append(p.get("title", ""))
                bits += [str(h) for h in p.get("highlights", []) or []]
    return _lower(" ".join(b for b in bits if b))


def _resume_text(resume):
    parts = [resume.get("summary", "")]
    parts += _resume_skill_terms(resume)
    for e in resume.get("experience", []) or []:
        if isinstance(e, dict):
            for p in e.get("positions", []) or []:
                parts += [str(h) for h in p.get("highlights", []) or []]
            parts += [str(h) for h in e.get("highlights", []) or []]
    for p in resume.get("projects", []) or []:
        if isinstance(p, dict):
            parts += [p.get("title", "")] + [str(h) for h in p.get("highlights", []) or []]
    return _lower(" ".join(p for p in parts if p))


def groundedness(resume, profile_state, profile_raw_text=""):
    """% of resume skills that are traceable to the candidate's profile (faithfulness)."""
    skills = _resume_skill_terms(resume)
    if not skills:
        return None, []
    known = _profile_known_text(profile_state, profile_raw_text)
    ungrounded = [s for s in skills if _lower(s) not in known]
    grounded = len(skills) - len(ungrounded)
    return round(100 * grounded / len(skills)), ungrounded


def ats_coverage(resume, required_skills):
    """% of the JD's required skills that appear anywhere in the resume."""
    req = [r for r in (required_skills or []) if r]
    if not req:
        return None, []
    text = _resume_text(resume)
    missing = [r for r in req if _lower(r) not in text]
    covered = len(req) - len(missing)
    return round(100 * covered / len(req)), missing


def skill_match_coverage(match):
    """% of required+preferred skills the candidate has (strong or partial)."""
    strong = len(match.get("strong_match", []))
    partial = len(match.get("partial_match", []))
    missing = len(match.get("missing_skills", []))
    total = strong + partial + missing
    if not total:
        return None
    return round(100 * (strong + partial) / total)


def guardrail_checks(resume, match):
    """Pass/fail checklist of the hard AI guardrails, evaluated on the outputs."""
    checks = []

    # 1. No internal system language leaked into the resume.
    text = _resume_text(resume)
    leaked = [p for p in INTERNAL_FLAG_PATTERNS if re.search(p, text)]
    checks.append(("Resume exposes no internal flags / scores", not leaked))

    # 2. Match score never 100% while required skills are missing.
    pct = match.get("match_percentage", 0)
    has_missing = bool(match.get("missing_skills"))
    checks.append(("Match % is honest (not 100% with gaps)", not (has_missing and pct >= 100)))

    # 3. Candidate identity present (no placeholder name).
    name = _lower(resume.get("name_line", ""))
    checks.append(("Candidate name present", bool(name) and name not in ("not available", "candidate")))

    # 4. Resume has real substance (summary + at least one section).
    has_body = bool(resume.get("summary")) and bool(
        resume.get("experience") or resume.get("projects") or resume.get("education"))
    checks.append(("Resume is complete (summary + sections)", has_body))

    # 5. Match provides reasoning (explainability guardrail).
    checks.append(("Match decision is explained", bool(match.get("match_explanation") or match.get("summary"))))

    return checks


def overall_score(quality):
    """Composite 0-100 from the available quality metrics (equal weight)."""
    vals = [v for v in quality if isinstance(v, (int, float))]
    return round(sum(vals) / len(vals)) if vals else None
