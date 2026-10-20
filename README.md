# AI Job Application Assistant with Application Tracker

A four-tab, Python-only Streamlit application that helps a job seeker analyse a job
description, intelligently match it against an **evolving** candidate profile, generate
a tailored resume PDF (ReportLab), track applications through their stages, analyse
rejections, and **learn from them** so every future application uses a refined
understanding of the candidate.

All AI behaviour is driven by **markdown agent-specification files** in `skills/` —
not by hardcoded prompts. Each user's data lives in **one JSON document in their own
browser session**; no database is used. Users keep their progress with the sidebar's
**Download my data** / **Restore saved data** buttons.

---

## Quick Start

```bash
pip install -r requirements.txt
streamlit run app.py
```

Each user picks an AI provider in the sidebar and pastes their own API key:

| Provider | Cost | Get a key | Default model | Embeddings |
|----------|------|-----------|---------------|------------|
| Google Gemini | Free tier (rate-limited) | <https://aistudio.google.com/apikey> | `gemini-3.8-flash` | `gemini-embedding-001` |
| Groq | Free tier (rate-limited) | <https://console.groq.com/keys> | `openai/gpt-oss-120b` | local fallback |
| OpenAI | Paid | <https://platform.openai.com/api-keys> | `gpt-4o` | `text-embedding-3-small` |
| xAI Grok | Paid (trial credits) | <https://console.x.ai> | `grok-4.3` | local fallback |

All four are called through their OpenAI-compatible APIs (`llm_provider.py`). The model
can be changed per provider under **Model** in the sidebar. Providers without an
embeddings API use a local text-similarity fallback for skill matching and job ranking.

For local development you can put an OpenAI key in `API.txt` (one line); it is
git-ignored and never deployed.

---

## Deploying to Streamlit Community Cloud

The app is multi-user safe: every visitor gets a private session workspace and uses
their own provider key, so no secrets need to be configured on the server.

1. Push this folder to a GitHub repository (`API.txt` and `data_store.json` are
   excluded by `.gitignore`).
2. Go to <https://share.streamlit.io>, sign in with GitHub, and click **Create app**.
3. Choose the repository and branch, set **Main file path** to `app.py`, and pick
   Python 3.12 or newer under **Advanced settings**.
4. Click **Deploy**. Share the resulting `*.streamlit.app` URL.

Notes:
* Session data is lost when a user closes/refreshes the page or the app restarts —
  users should download their data to continue later.
* Free Streamlit apps sleep after a period of inactivity and wake on the next visit.

---

## File Map

| File | Responsibility |
|------|----------------|
| `app.py` | Streamlit UI: 4 tabs, header, Clear-All-Data + confirmation dialogs |
| `ai_engine.py` | Loads skill `.md` files fresh, runs each agent, parses strict JSON |
| `rag_pipeline.py` | Text extraction (`.txt/.pdf/.docx`), chunking, embeddings, cosine similarity, semantic skill signal |
| `resume_generator.py` | Renders the tailored resume into a clean PDF with ReportLab |
| `data_store.py` | Per-session JSON store + download/restore: living profile, profile history, applications |
| `skills/*.md` | Agent specification files (see below) |
| `Final Prompt.txt` | The single detailed build prompt used to generate the app |

### Agent specification files (`skills/`)

| File | Agent | Role |
|------|-------|------|
| `agent-rules.md` | (all) | Global rules: grounding, the living profile, output discipline, tone |
| `profile-analyst-skill.md` | Profile Analyst | Build the first `current_profile_state` from the uploaded document |
| `skill-matcher-skill.md` | Skill Matcher | JD analysis + level-aware semantic match + honest 0–100 score |
| `resume-writer-skill.md` | Resume Writer | Tailored resume content; de-emphasises weakened skills; no internal flags |
| `rejection-analyst-skill.md` | Rejection Analyst | Per-rejection analysis + precise profile update |
| `global-analyst-skill.md` | Global Analyst | Cross-rejection patterns + recommendation |

Each agent call assembles its system prompt as **global rules + that skill card**, then
forces strict JSON output. Editing a `.md` file changes the agent's behaviour with no
Python changes — this is where the "agent-ness" lives.

---

## 1 · System Architecture Overview

```
            ┌──────────────────────── app.py (Streamlit, 4 tabs) ────────────────────────┐
            │  Apply        Tracking        Not Selected        Global Analysis            │
            └───────┬───────────┬────────────────┬───────────────────┬────────────────────┘
                    │           │                │                   │
              ai_engine.py (reads skills/*.md fresh, calls gpt-4o, parses JSON)
                    │           │                │                   │
   ┌────────────────┴───┐   ┌───┴──────┐   ┌─────┴───────┐   ┌────────┴────────┐
   │ rag_pipeline.py    │   │ resume_  │   │ data_store  │   │ skills/*.md      │
   │ extract/chunk/embed│   │ generator│   │ (JSON)      │   │ agent specs      │
   └────────────────────┘   └──────────┘   └─────────────┘   └─────────────────┘
```

* **One JSON document per user session** holds everything: `current_profile_state`, `profile_history`,
  every `application` (with `status_history`, `rejection_notes`, `rejection_analysis`),
  and the last `global_analysis`.
* **Six agents**, all configured by markdown, all grounded in their inputs.
* **A RAG layer** does multi-format text extraction and embedding-based semantic
  similarity so matching is meaning-aware, not keyword-aware.

---

## 2 · Skill Matching & How the Internal Profile Evolves

**Matching (beyond keywords).** When you run a match:
1. The Skill Matcher quickly extracts the JD's skills.
2. `rag_pipeline.semantic_skill_signal` embeds JD skills and profile skills
   (`text-embedding-3-small`) and, for each JD skill, finds the most semantically
   similar profile skill and its **current level**. So `ML` ≈ `Machine Learning`.
3. The Skill Matcher agent turns that signal into `strong` / `moderate` / `missing`
   buckets and an honest 0–100 score, weighting by skill level
   (`strong=1.0, moderate=0.6, weak=0.3`), project relevance, and experience alignment.

**Evolution.** `current_profile_state` starts as the Profile Analyst's reading of the
uploaded document. After each rejection analysis (Tab 3), `data_store.apply_profile_update`:
* downgrades the level of skills the rejection gave evidence for (only ever downward),
* appends new `learning_areas`,
* records the change in `profile_history`.

Because Tab 1 reads `current_profile_state`, the **next** application automatically
reflects the refined understanding — a skill downgraded after a rejection now counts
for less in the match and is de-emphasised in the resume.

---

## 3 · Resume Generation & Updated-Profile Influence

The Resume Writer agent receives the **evolving** profile, the skill-match result, the
JD analysis, and (optionally) a `reference_style` extracted from an uploaded reference
resume. It returns structured content (summary, reordered skills, relevant projects,
section order). `resume_generator.py` renders that into a clean PDF with ReportLab.

* Skills weakened through rejections are presented modestly or omitted from the
  highlights — never emphasised.
* If a reference resume was uploaded, its **section ordering and tone** are mirrored
  (style only; content is never copied). Otherwise a default professional template
  is used.
* The PDF never contains internal system language — no scores, levels, flags, or
  annotations. It always reads as a normal professional resume.

---

## 4 · How Global Analysis Works Across Rejections

Tab 4 sends **all** analysed rejections to the Global Analyst, which surfaces only
items that appear in **more than one** rejection:
* `recurring_skill_gaps` — gaps repeated across multiple companies,
* `common_unanswered_topics` — questions/subjects that came up repeatedly,
* `consistent_weak_areas` — experience domains weak across applications,
* `summary_recommendation` — one prioritised, plain-language paragraph on what to fix
  before the next round.

Until at least two rejections have been analysed, the tab shows a meaningful empty
state instead of inventing patterns.

---

## Expected User Flow

Upload profile (+ optional reference resume) → paste JD → Extract & Match → Prepare
Resume (after 7 conditions) → Download → apply offline → **Submitted** → Tracking →
Interview / Selected / **Not Selected** (with confirmation) → enter rejection notes →
**Analyze** (profile evolves) → Global Analysis reflects consolidated patterns → the
next application in Tab 1 uses the updated profile.

---

## Limitations (for the demo discussion)

* Submission is **simulated** — the app never applies to a company; the user applies
  offline and clicks *Submitted* to log it.
* Extraction quality depends on the uploaded document; scanned/image PDFs without a
  text layer will not extract well.
* Matching and analysis quality depend on the LLM and the richness of the JD/notes.
* Embeddings and chat calls require network access and a valid key for the chosen
  provider. Free tiers have per-minute and per-day limits; a busy session may need to
  wait a minute between actions.
