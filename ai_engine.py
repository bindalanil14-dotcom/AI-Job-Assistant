"""
AI engine for the Job Application Assistant.

All agent behaviour is defined in markdown specification files under skills/ and the
global guardrails (skills/guardrails.md) + rules (skills/agent-rules.md). Each agent
call assembles: guardrails + global rules + that agent's skill card, and is sent to
the modular LLM provider (llm_provider.py) — so OpenAI can be swapped for another
provider with no change here.

Context minimisation (token guardrails): each agent receives ONLY its allowed inputs.
- Profile Extraction : candidate profile text only
- JD Analysis        : job description only
- Skill Matching     : current profile state + extracted JD JSON (+ semantic signal)
- Resume Generation  : current profile state + JD summary + reference style
- Rejection Analysis : current profile state + JD + skill match + rejection notes
- Global Analysis    : rejection-analysis summaries only
Large inputs are truncated to stay within suggested token limits.
"""

import os
import re

from llm_provider import get_provider
from rag_pipeline import semantic_skill_signal

SKILLS_DIR = "skills"
GUARDRAILS_FILE = os.path.join(SKILLS_DIR, "guardrails.md")
RULES_FILE = os.path.join(SKILLS_DIR, "agent-rules.md")

# Suggested input budgets (chars ≈ 4 * tokens) to keep calls lightweight.
LIMIT_PROFILE = 6000     # ~1500 tokens
LIMIT_JD = 4800          # ~1200 tokens
LIMIT_NOTES = 4000


# ----------------------------------------------------------------------------
# SKILL FILE LOADING + LLM PLUMBING
# ----------------------------------------------------------------------------
def read_skill_file(filename):
    """Read a markdown skill/rule file, stripping YAML frontmatter if present."""
    path = filename if os.path.isabs(filename) else os.path.join(SKILLS_DIR, filename)
    if not os.path.exists(path) and os.path.exists(filename):
        path = filename
    try:
        with open(path, "r", encoding="utf-8") as f:
            content = f.read()
        content = re.sub(r"^---\s*\n.*?\n---\s*\n", "", content, flags=re.DOTALL)
        return content.strip()
    except Exception as e:
        return f"Error reading skill file '{filename}': {str(e)}"


def _system(skill_filename):
    """Guardrails + global rules + the specific skill card = full system prompt."""
    guardrails = read_skill_file(GUARDRAILS_FILE)
    rules = read_skill_file(RULES_FILE)
    card = read_skill_file(skill_filename)
    return f"{guardrails}\n\n---\n\n{rules}\n\n---\n\n{card}"


def _chat_json(api_key, skill_filename, user_payload, temperature=0.1, max_tokens=1500):
    provider = get_provider(api_key)
    label = os.path.basename(skill_filename).replace("-skill.md", "").replace(".md", "")
    return provider.chat_json(_system(skill_filename), user_payload,
                              temperature=temperature, max_tokens=max_tokens, label=label)


def _truncate(text, limit):
    text = text or ""
    return text[:limit]


def _dumps(obj):
    import json
    return json.dumps(obj, ensure_ascii=False)


# ----------------------------------------------------------------------------
# PROFILE ANALYST  (input: profile text only)
# ----------------------------------------------------------------------------
def analyze_profile(api_key, profile_text):
    payload = (
        "Analyse the following candidate profile text and return the COMPLETE "
        "structured profile state. Capture every work role with all its detail "
        "(multiple achievement bullets each), all education, all certifications and "
        "projects — this profile is the sole source for a full resume later.\n\n"
        "=== PROFILE TEXT ===\n" + _truncate(profile_text, LIMIT_PROFILE)
    )
    result = _chat_json(api_key, "profile-analyst-skill.md", payload,
                        temperature=0.1, max_tokens=2500)
    result.setdefault("name", "")
    result.setdefault("contact", {})
    result.setdefault("skills", [])
    result.setdefault("tools", [])
    result.setdefault("experience_level", "junior")
    result.setdefault("experience", [])
    result.setdefault("education", [])
    result.setdefault("certifications", [])
    result.setdefault("projects", [])
    result.setdefault("domains", [])
    result.setdefault("learning_areas", [])
    return result


# ----------------------------------------------------------------------------
# JD ANALYST  (input: job description only)
# ----------------------------------------------------------------------------
def analyze_jd(api_key, jd_text):
    payload = (
        "Analyse this job description and return the structured summary specified "
        "in your skill card.\n\n=== JOB DESCRIPTION ===\n" + _truncate(jd_text, LIMIT_JD)
    )
    result = _chat_json(api_key, "jd-analyst-skill.md", payload,
                        temperature=0.0, max_tokens=900)
    result.setdefault("company_name", "Not Available")
    result.setdefault("job_role", "Not Available")
    result.setdefault("required_skills", [])
    result.setdefault("preferred_skills", [])
    result.setdefault("technologies", [])
    result.setdefault("responsibilities", [])
    result.setdefault("seniority", "")
    return result


# ----------------------------------------------------------------------------
# REFERENCE RESUME STYLE  (format only — never content)
# ----------------------------------------------------------------------------
def extract_reference_style(api_key, reference_text):
    """Thoroughly analyse the FORMAT of a reference resume (never its content)."""
    provider = get_provider(api_key)
    system = (
        "You analyse only the FORMAT and STRUCTURE of a resume — never its content. "
        "From the reference resume text, return strict JSON:\n"
        '{"section_order": ["summary","experience","skills","education","projects","certifications"], '
        '"tone": "concise|narrative|formal", '
        '"density": "concise|detailed", '
        '"header_style": "left|center", '
        '"bullet_style": "bullets|paragraph", '
        '"uses_dividers": true, '
        '"date_format": "e.g. Jan 2021 - Mar 2024", '
        '"notes": "one sentence on overall layout"}. '
        "List sections in the exact order they appear. Set density to 'detailed' if "
        "roles have multiple bullet points. Do NOT include any of the person's actual "
        "skills, names, employers, or other content."
    )
    try:
        result = provider.chat_json(system, _truncate(reference_text, 6000),
                                    temperature=0.1, max_tokens=500)
    except Exception:
        return None
    result.setdefault("section_order", [])
    result.setdefault("tone", "formal")
    result.setdefault("density", "detailed")
    result.setdefault("header_style", "left")
    result.setdefault("bullet_style", "bullets")
    return result


# ----------------------------------------------------------------------------
# SKILL MATCHER  (input: current profile state + extracted JD JSON + signal)
# ----------------------------------------------------------------------------
def match_skills(api_key, jd_json, profile_state):
    """
    Semantic, level-aware match. Receives the already-extracted JD JSON (NOT raw JD,
    not the resume, not application/rejection history) per the token guardrails.
    """
    jd_skills = list(jd_json.get("required_skills", [])) + list(jd_json.get("preferred_skills", []))
    profile_skills = profile_state.get("skills", [])
    signal = semantic_skill_signal(jd_skills, profile_skills, api_key)

    payload = {
        "extracted_jd": {
            "job_role": jd_json.get("job_role", ""),
            "required_skills": jd_json.get("required_skills", []),
            "preferred_skills": jd_json.get("preferred_skills", []),
            "responsibilities": jd_json.get("responsibilities", []),
            "seniority": jd_json.get("seniority", ""),
        },
        "current_profile_state": profile_state,
        "semantic_similarity_signal": signal,
    }
    user_payload = (
        "Match the extracted JD against current_profile_state using the "
        "semantic_similarity_signal so matching goes beyond exact keywords. "
        "Categorise as Strong / Partial / Missing and give brief reasoning. "
        "Return the strict JSON specified in your skill card.\n\n" + _dumps(payload)
    )
    result = _chat_json(api_key, "skill-matcher-skill.md", user_payload,
                        temperature=0.1, max_tokens=900)

    result.setdefault("strong_match", [])
    result.setdefault("partial_match", [])
    result.setdefault("missing_skills", [])
    result.setdefault("match_explanation", result.get("summary", ""))
    try:
        pct = int(round(float(result.get("match_percentage", 0))))
    except Exception:
        pct = 0
    pct = max(0, min(100, pct))
    # Guardrail: never 100% (and cap high) while any required skill is missing.
    if result["missing_skills"]:
        pct = min(pct, 95)
    result["match_percentage"] = pct
    return result


# ----------------------------------------------------------------------------
# RESUME WRITER  (input: current profile state + JD summary + reference style)
# ----------------------------------------------------------------------------
def write_resume_content(api_key, profile_state, jd_summary, reference_style=None):
    candidate_name = (profile_state or {}).get("name", "") or ""
    candidate_contact = (profile_state or {}).get("contact", {}) or {}

    payload = {
        "candidate_name": candidate_name,
        "candidate_contact": candidate_contact,
        "current_profile_state": profile_state,   # complete structured profile
        "jd_summary": jd_summary,                  # role + skills + responsibilities
        "reference_style": reference_style,        # format only
    }
    user_payload = (
        "Write a COMPLETE, DETAILED, ATS-friendly resume in the FIXED professional "
        "structure from your skill card, using ONLY the candidate's real data in "
        "current_profile_state. Group skills into labelled technical_skills categories; "
        "give every employer its positions with 3–5 metric-driven achievement bullets; "
        "include education, projects (with bullets) and certifications. Make it "
        "substantial (about 1–2 pages) but never fabricate or exaggerate. Set name_line "
        "and the contact object from the candidate fields. Reorder to lead with the JD's "
        "important skills, and never expose internal flags or scores. Return the strict "
        "JSON specified in your skill card.\n\n" + _dumps(payload)
    )
    result = _chat_json(api_key, "resume-writer-skill.md", user_payload,
                        temperature=0.2, max_tokens=3200)

    result.setdefault("name_line", "")
    result.setdefault("contact", {})
    result.setdefault("contact_line", "")
    result.setdefault("summary", "")
    result.setdefault("technical_skills", [])
    result.setdefault("skills", [])
    result.setdefault("experience", [])
    result.setdefault("education", [])
    result.setdefault("certifications", [])
    result.setdefault("projects", [])
    result.setdefault("section_order",
                      ["summary", "technical_skills", "experience", "education",
                       "projects", "certifications"])

    # Deterministic identity safety net from the structured profile.
    placeholder = {"", "candidate", "candidate name", "your name", "name"}
    if result["name_line"].strip().lower() in placeholder and candidate_name:
        result["name_line"] = candidate_name
    if not result.get("contact") and candidate_contact:
        result["contact"] = candidate_contact
    if not result["name_line"].strip():
        result["name_line"] = candidate_name or "Not Available"

    # Backfill structured sections if the writer omitted them but the profile has them.
    for key in ("experience", "education", "certifications", "projects"):
        if not result.get(key) and (profile_state or {}).get(key):
            result[key] = profile_state[key]
    return result


# ----------------------------------------------------------------------------
# REJECTION ANALYST  (input: profile state + JD + skill match + notes)
# ----------------------------------------------------------------------------
def analyze_rejection(api_key, rejection_notes, application, profile_state):
    payload = {
        "rejection_notes": _truncate(rejection_notes, LIMIT_NOTES),
        "jd": _truncate(application.get("jd", ""), LIMIT_JD),
        "skill_match": {
            "match_percentage": application.get("match_percentage"),
            "strong_match": application.get("strong_match", []),
            "partial_match": application.get("partial_match", []),
            "missing_skills": application.get("missing_skills", []),
        },
        "current_profile_state": profile_state,
    }
    user_payload = (
        "Analyse this single rejection using only the evidence provided and propose a "
        "precise update to current_profile_state. Suggest learning areas; only "
        "downgrade skills the notes give evidence for. Return the strict JSON "
        "specified in your skill card.\n\n" + _dumps(payload)
    )
    result = _chat_json(api_key, "rejection-analyst-skill.md", user_payload,
                        temperature=0.1, max_tokens=900)
    result.setdefault("analysis_summary", "")
    result.setdefault("skill_gaps", [])
    result.setdefault("struggled_topics", [])
    result.setdefault("weaker_experience_areas", [])
    result.setdefault("downgrades", [])
    result.setdefault("new_learning_areas", [])
    return result


# ----------------------------------------------------------------------------
# GLOBAL ANALYST  (input: rejection-analysis summaries only)
# ----------------------------------------------------------------------------
def run_global_analysis(api_key, rejected_apps, profile_state=None):
    summaries = []
    for app in rejected_apps:
        ra = app.get("rejection_analysis") or {}
        summaries.append({
            "role": app.get("job_role", ""),
            "analysis_summary": ra.get("analysis_summary", ""),
            "skill_gaps": ra.get("skill_gaps", []),
            "struggled_topics": ra.get("struggled_topics", []),
            "weaker_experience_areas": ra.get("weaker_experience_areas", []),
        })
    payload = {"rejection_summaries": summaries}
    user_payload = (
        "Look across ALL of these rejection-analysis summaries and surface patterns "
        "that recur across MORE THAN ONE rejection. Return the strict JSON specified "
        "in your skill card.\n\n" + _dumps(payload)
    )
    result = _chat_json(api_key, "global-analyst-skill.md", user_payload,
                        temperature=0.2, max_tokens=900)
    result.setdefault("recurring_skill_gaps", [])
    result.setdefault("frequently_asked_topics", [])
    result.setdefault("weak_domains", [])
    result.setdefault("learning_roadmap", "")
    result.setdefault("final_recommendation", "")

    # Deterministic safety net: if the model left a list empty but the rejection
    # summaries do contain items, consolidate them (most-frequent first) so the
    # report is never blank when real gaps exist.
    def _consolidate(field):
        counts, order = {}, []
        for s in summaries:
            for item in s.get(field, []):
                if not item:
                    continue
                key = str(item).strip().lower()
                if key not in counts:
                    counts[key] = [0, str(item).strip()]
                    order.append(key)
                counts[key][0] += 1
        return [counts[k][1] for k in sorted(order, key=lambda k: -counts[k][0])]

    if not result["recurring_skill_gaps"]:
        result["recurring_skill_gaps"] = _consolidate("skill_gaps")
    if not result["frequently_asked_topics"]:
        result["frequently_asked_topics"] = _consolidate("struggled_topics")
    if not result["weak_domains"]:
        result["weak_domains"] = _consolidate("weaker_experience_areas")
    return result
