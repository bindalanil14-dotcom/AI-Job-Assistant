"""
Job search / fetch layer  (pluggable providers).
=================================================================
Fetches LIVE job postings from public job-board feeds and ranks them against the
candidate's resume using embeddings. Built like llm_provider: each source is a small
function registered in PROVIDERS, so sources are interchangeable / extensible.

Why these sources: portals such as AmbitionBox, Naukri and LinkedIn block automated
access (anti-bot, login walls) and their Terms of Service prohibit scraping. This
layer therefore fetches from job boards that expose public programmatic feeds
(Remotive, Arbeitnow, RemoteOK). A new portal adapter can be added by writing one
fetch_* function and registering it. If the network is unavailable or every source
returns nothing, a curated SAMPLE set keeps the feature usable for the demo.
"""

import re

from rag_pipeline import cosine_similarity
from llm_provider import get_provider

UA = "Mozilla/5.0 (compatible; JobApplicationAssistant/1.0)"


# ----------------------------------------------------------------------------
# HELPERS
# ----------------------------------------------------------------------------
def _http_get_json(url, params=None, timeout=12):
    import requests
    r = requests.get(url, params=params, timeout=timeout,
                     headers={"User-Agent": UA, "Accept": "application/json"})
    r.raise_for_status()
    return r.json()


def _strip_html(text):
    text = re.sub(r"<[^>]+>", " ", text or "")
    text = text.replace("&amp;", "&").replace("&nbsp;", " ").replace("&#39;", "'")
    return re.sub(r"\s+", " ", text).strip()


def _posting(title, company, location, url, description, source, tags=None, salary=""):
    return {
        "title": title or "",
        "company": company or "",
        "location": location or "",
        "url": url or "",
        "description": (description or "")[:6000],
        "source": source,
        "tags": tags or [],
        "salary": salary or "",
    }


def _matches(query, *texts):
    if not query:
        return True
    blob = " ".join(t for t in texts if t).lower()
    words = [w for w in query.lower().split() if len(w) > 1]
    return any(w in blob for w in words) if words else True


# ----------------------------------------------------------------------------
# PROVIDERS (each returns a list of postings; raises on network error)
# ----------------------------------------------------------------------------
def fetch_remotive(query, limit=20):
    data = _http_get_json("https://remotive.com/api/remote-jobs",
                          {"search": query, "limit": limit})
    out = []
    for j in data.get("jobs", [])[:limit]:
        out.append(_posting(
            j.get("title"), j.get("company_name"),
            j.get("candidate_required_location") or "Remote", j.get("url"),
            _strip_html(j.get("description", "")), "Remotive",
            j.get("tags", []), j.get("salary", "")))
    return out


def fetch_arbeitnow(query, limit=20):
    data = _http_get_json("https://www.arbeitnow.com/api/job-board-api")
    out = []
    for j in data.get("data", []):
        if not _matches(query, j.get("title", ""), " ".join(j.get("tags", []))):
            continue
        loc = j.get("location") or ("Remote" if j.get("remote") else "")
        out.append(_posting(
            j.get("title"), j.get("company_name"), loc, j.get("url"),
            _strip_html(j.get("description", "")), "Arbeitnow", j.get("tags", [])))
        if len(out) >= limit:
            break
    return out


def fetch_remoteok(query, limit=20):
    data = _http_get_json("https://remoteok.com/api")
    out = []
    for j in data:
        if not isinstance(j, dict) or not j.get("position"):
            continue
        if not _matches(query, j.get("position", ""), " ".join(j.get("tags", []))):
            continue
        out.append(_posting(
            j.get("position"), j.get("company"), j.get("location") or "Remote",
            j.get("url"), _strip_html(j.get("description", "")), "RemoteOK",
            j.get("tags", []), ""))
        if len(out) >= limit:
            break
    return out


PROVIDERS = {
    "Remotive": fetch_remotive,
    "Arbeitnow": fetch_arbeitnow,
    "RemoteOK": fetch_remoteok,
}


# ----------------------------------------------------------------------------
# CURATED SAMPLE FALLBACK (used only if the network/sources return nothing)
# ----------------------------------------------------------------------------
SAMPLE_JOBS = [
    _posting("Machine Learning Engineer", "Nimbus AI", "Remote",
             "https://example.com/jobs/mle",
             "Build and deploy ML models in production. Required: Python, machine "
             "learning, model deployment, Docker, REST APIs. Nice to have: Kubernetes, "
             "AWS. Work with data scientists to ship recommendation and ranking systems.",
             "Sample"),
    _posting("Full Stack Engineer", "Orbit Labs", "Bangalore, India",
             "https://example.com/jobs/fse",
             "Develop React frontends and Java Spring Boot microservices. Required: "
             "React, TypeScript, Java, Spring Boot, REST APIs, CI/CD. Experience with "
             "Docker and cloud deployment preferred.",
             "Sample"),
    _posting("Data Scientist", "Quanta Insights", "Remote",
             "https://example.com/jobs/ds",
             "Analyse large datasets and build predictive models. Required: Python, "
             "Pandas, SQL, statistics, machine learning. Communicate findings to "
             "stakeholders. Tableau or Power BI a plus.",
             "Sample"),
    _posting("AI Product Manager", "Vertex Product", "Mumbai, India",
             "https://example.com/jobs/aipm",
             "Own the roadmap for AI features. Required: product management, PRD "
             "writing, stakeholder management, understanding of LLMs and RAG, Agile. "
             "Translate AI capabilities into business outcomes.",
             "Sample"),
    _posting("GenAI Engineer", "Synapse Systems", "Remote",
             "https://example.com/jobs/genai",
             "Build RAG pipelines and agentic workflows. Required: Python, LangChain, "
             "vector databases, prompt engineering, OpenAI API, LLM observability. "
             "Deploy with Docker and monitor with LangSmith/Langfuse.",
             "Sample"),
    _posting("Backend Engineer", "Cobalt Cloud", "Remote",
             "https://example.com/jobs/be",
             "Design scalable backend services. Required: Python or Java, FastAPI or "
             "Spring Boot, PostgreSQL, REST APIs, Docker, CI/CD. Build reliable APIs "
             "for high-traffic systems.",
             "Sample"),
]


# ----------------------------------------------------------------------------
# SEARCH + RANK
# ----------------------------------------------------------------------------
def search_jobs(query, sources=None, limit_per_source=12):
    """Fetch & dedupe postings from the chosen sources. Falls back to samples."""
    sources = sources or list(PROVIDERS.keys())
    seen, results, errors = set(), [], []
    for name in sources:
        fn = PROVIDERS.get(name)
        if not fn:
            continue
        try:
            for job in fn(query, limit_per_source):
                key = (job["title"].lower().strip(), job["company"].lower().strip())
                if key in seen:
                    continue
                seen.add(key)
                results.append(job)
        except Exception as e:  # noqa: BLE001 - network/site issues are expected
            errors.append(f"{name}: {e}")

    used_fallback = False
    if not results:
        used_fallback = True
        for job in SAMPLE_JOBS:
            if _matches(query, job["title"], job["description"]):
                results.append(job)
        if not results:  # query matched nothing in samples -> show all samples
            results = list(SAMPLE_JOBS)
    return results, errors, used_fallback


def _profile_query_text(profile_state):
    skills = [s.get("name", "") for s in profile_state.get("skills", [])]
    titles = []
    for e in profile_state.get("experience", []):
        if isinstance(e, dict):
            titles.append(e.get("role") or e.get("title") or "")
    bits = titles + skills + profile_state.get("domains", [])
    return " ".join(b for b in bits if b)[:1500]


def rank_jobs(jobs, profile_state, api_key):
    """
    Score each job's resume fit by BLENDING two signals so the ranking truly tracks
    the resume:
      - skill overlap: how many of the candidate's actual skills/tools the posting mentions
      - semantic similarity: embedding cosine between the profile and the posting
    Returns jobs sorted by fit (desc).
    """
    if not jobs:
        return jobs

    terms = set()
    for s in profile_state.get("skills", []):
        n = (s.get("name") or "").strip().lower()
        if len(n) > 1:
            terms.add(n)
    for t in profile_state.get("tools", []):
        if isinstance(t, str) and len(t.strip()) > 1:
            terms.add(t.strip().lower())

    profile_text = _profile_query_text(profile_state) or "professional"
    job_texts = [f"{j['title']} {' '.join(j.get('tags', []))} {j['description'][:600]}"
                 for j in jobs]
    try:
        embs = get_provider(api_key).embed([profile_text] + job_texts)
        sims = [cosine_similarity(embs[0], e) for e in embs[1:]]
    except Exception:
        sims = [0.0] * len(jobs)

    lo, hi = (min(sims), max(sims)) if sims else (0, 0)
    for j, sim, blob in zip(jobs, sims, job_texts):
        sem = 0.5 if hi == lo else (sim - lo) / (hi - lo)  # 0..1 within this result set
        text = blob.lower()
        hits = sum(1 for t in terms if t in text)
        # Overlap as a fraction of the candidate's skills, with diminishing returns.
        overlap = min(1.0, hits / 6.0) if terms else 0.0
        score = 0.6 * overlap + 0.4 * sem            # skill overlap weighted higher
        j["fit"] = int(round(40 + 59 * min(1.0, score)))   # readable 40–99 band
        j["matched_skills"] = [t for t in terms if t in text][:8]
    jobs.sort(key=lambda x: (x.get("fit") or 0), reverse=True)
    return jobs
