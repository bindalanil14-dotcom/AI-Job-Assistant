"""
AI Job Application Assistant with Application Tracker
=====================================================
A four-tab Streamlit application:

  Tab 1 : Apply          - profile/reference/JD inputs, skill match, resume, submit
  Tab 2 : Tracking       - status pipeline for every active application
  Tab 3 : Not Selected   - per-rejection notes + AI analysis that evolves the profile
  Tab 4 : Global Analysis - cross-rejection patterns and recommendations

All AI behaviour comes from markdown specification files in skills/.
Each user's data lives in one JSON document in their own session; they can
download it and restore it later from the sidebar.
"""

import os
import uuid
from datetime import datetime

import streamlit as st

from rag_pipeline import extract_text_from_upload, extract_colors_from_upload
from data_store import (
    load_data, save_data, clear_all_data, set_initial_profile,
    apply_profile_update, add_application, get_application, set_status,
    active_applications, rejected_applications, export_json, import_json,
)
from ai_engine import (
    analyze_profile, analyze_jd, extract_reference_style, match_skills,
    write_resume_content, analyze_rejection, run_global_analysis,
)
from resume_generator import generate_resume_pdf
from job_search import search_jobs, rank_jobs, PROVIDERS
from llm_provider import (
    get_metrics, reset_metrics, estimate_cost, PROVIDERS, DEFAULT_PROVIDER,
    PROVIDER_CHOICE_KEY, api_key_state_key, model_state_key,
)
import evaluation

# ----------------------------------------------------------------------------
# PAGE CONFIG
# ----------------------------------------------------------------------------
st.set_page_config(
    page_title="AI Job Application Assistant",
    page_icon="🧭",
    layout="wide",
    initial_sidebar_state="expanded",
)

# Local-development convenience only: if API.txt exists next to the app it is used
# when the user hasn't typed a key. It is git-ignored, so it never reaches a deployment.
API_KEY_PATH = "API.txt"


def _now():
    return datetime.now().isoformat(timespec="seconds")


def _local_api_key():
    if os.path.exists(API_KEY_PATH):
        with open(API_KEY_PATH, "r", encoding="utf-8") as f:
            return f.read().strip()
    return ""

# ----------------------------------------------------------------------------
# THEME SYSTEM (Light default + Dark toggle, modern look)
# ----------------------------------------------------------------------------
THEMES = {
    "Light": {
        "bg": "#f3f6f8", "bg2": "#dbe9f6", "surface": "#ffffff", "surface2": "#eef3f8",
        "text": "#1d2226", "muted": "#56687a", "border": "#dce3ea",
        "shadow": "0 8px 24px rgba(10,102,194,0.10)", "input_bg": "#ffffff",
        "strong_bg": "#e3f7ec", "strong_fg": "#16794a",
        "mod_bg": "#fff3da", "mod_fg": "#9a6a12",
        "miss_bg": "#ffe6ea", "miss_fg": "#c1304b",
    },
    "Dark": {
        "bg": "#0a0e14", "bg2": "#10243b", "surface": "#15202b", "surface2": "#1b2a38",
        "text": "#e7edf3", "muted": "#9fb0c0", "border": "#26343f",
        "shadow": "0 12px 34px rgba(0,0,0,0.45)", "input_bg": "#1b2a38",
        "strong_bg": "rgba(34,197,94,0.16)", "strong_fg": "#5ee79b",
        "mod_bg": "rgba(245,180,40,0.16)", "mod_fg": "#ffce6e",
        "miss_bg": "rgba(244,63,94,0.16)", "miss_fg": "#ff8fa3",
    },
}


def inject_theme(mode):
    t = THEMES.get(mode, THEMES["Light"])
    # LinkedIn-inspired professional blue gradient.
    grad = "linear-gradient(135deg,#004182 0%,#0a66c2 55%,#3a9bdc 100%)"
    st.markdown(
        f"""
        <style>
        @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=Space+Grotesk:wght@500;600;700&display=swap');

        :root {{
            --bg:{t['bg']}; --bg2:{t['bg2']}; --surface:{t['surface']}; --surface2:{t['surface2']};
            --text:{t['text']}; --muted:{t['muted']}; --border:{t['border']}; --shadow:{t['shadow']};
            --grad:{grad};
        }}

        .stApp {{
            background:
              radial-gradient(900px 500px at 100% -10%, var(--bg2) 0%, transparent 60%),
              radial-gradient(700px 400px at -10% 0%, var(--bg2) 0%, transparent 55%),
              var(--bg) !important;
            font-family:'Inter',sans-serif;
            color:var(--text);
        }}
        .stApp, .stApp p, .stApp span, .stApp label, .stApp li, .stApp div {{ color:var(--text); }}
        h1,h2,h3,h4 {{ font-family:'Space Grotesk','Inter',sans-serif !important; color:var(--text) !important;
                       letter-spacing:-0.5px; }}
        .stCaption, [data-testid="stCaptionContainer"] {{ color:var(--muted) !important; }}

        /* ---- Hero ---- */
        .hero {{
            background:var(--grad); border-radius:22px; padding:26px 30px; margin:4px 0 18px 0;
            box-shadow:var(--shadow); position:relative; overflow:hidden;
        }}
        .hero:after {{ content:""; position:absolute; right:-40px; top:-40px; width:180px; height:180px;
            background:rgba(255,255,255,0.18); border-radius:50%; }}
        .hero h1 {{ color:#fff !important; margin:0; font-size:2.05rem; }}
        .hero p {{ color:rgba(255,255,255,0.92) !important; margin:6px 0 0 0; font-size:0.98rem; font-weight:500; }}
        .hero .chips {{ margin-top:14px; display:flex; gap:8px; flex-wrap:wrap; }}
        .hero .chip {{ background:rgba(255,255,255,0.18); color:#fff !important; padding:5px 12px;
            border-radius:999px; font-size:0.78rem; font-weight:600; backdrop-filter:blur(4px); }}

        /* ---- Cards ---- */
        .card {{ background:var(--surface); border:1px solid var(--border); border-radius:18px;
            padding:18px 20px; margin-bottom:16px; box-shadow:var(--shadow); }}

        /* ---- Pills / badges ---- */
        .pill {{ display:inline-block; padding:4px 12px; border-radius:999px; font-size:0.78rem;
            font-weight:600; margin:2px 4px 2px 0; }}
        .pill-strong {{ background:{t['strong_bg']}; color:{t['strong_fg']} !important; }}
        .pill-mod {{ background:{t['mod_bg']}; color:{t['mod_fg']} !important; }}
        .pill-miss {{ background:{t['miss_bg']}; color:{t['miss_fg']} !important; }}
        .ok-badge {{ background:{t['strong_bg']}; color:{t['strong_fg']} !important; padding:5px 12px;
            border-radius:999px; font-weight:600; font-size:0.82rem; }}
        .status-submitted {{ color:{t['strong_fg']} !important; font-weight:700; }}
        .blink-dot {{ height:9px; width:9px; background:{t['strong_fg']}; border-radius:50%;
            display:inline-block; margin-right:6px; box-shadow:0 0 0 0 {t['strong_fg']};
            animation:pulse 1.4s infinite; }}
        @keyframes pulse {{ 0%{{box-shadow:0 0 0 0 {t['strong_fg']};opacity:1;}}
            70%{{box-shadow:0 0 0 8px transparent;opacity:0.6;}} 100%{{opacity:1;}} }}

        /* ---- Buttons ---- */
        .stButton > button {{ border-radius:12px !important; font-weight:600 !important;
            border:1px solid var(--border) !important; background:var(--surface) !important;
            color:var(--text) !important; transition:transform .12s ease, box-shadow .12s ease; }}
        .stButton > button:hover:not(:disabled) {{ transform:translateY(-1px);
            box-shadow:0 6px 16px rgba(10,102,194,0.25) !important; border-color:#0a66c2 !important; }}
        .stButton > button[kind="primary"] {{ background:var(--grad) !important; color:#fff !important;
            border:none !important; }}
        .stDownloadButton > button {{ border-radius:12px !important; font-weight:600 !important;
            background:var(--grad) !important; color:#fff !important; border:none !important; }}

        /* ---- Inputs ---- */
        .stTextInput input, .stTextArea textarea, [data-baseweb="input"], [data-baseweb="textarea"] {{
            background:var(--surface) !important; color:var(--text) !important;
            border-radius:12px !important; }}
        [data-testid="stFileUploaderDropzone"] {{ background:var(--surface2) !important;
            border-radius:14px !important; border:1px dashed var(--border) !important; }}

        /* ---- Tabs as pills ---- */
        .stTabs [data-baseweb="tab-list"] {{ gap:8px; background:transparent; }}
        .stTabs [data-baseweb="tab"] {{ background:var(--surface); border:1px solid var(--border);
            border-radius:999px; padding:8px 18px; font-weight:600; color:var(--muted); }}
        .stTabs [aria-selected="true"] {{ background:var(--grad) !important; color:#fff !important;
            border:none !important; }}
        .stTabs [data-baseweb="tab-highlight"] {{ display:none; }}

        /* ---- Sidebar ---- */
        [data-testid="stSidebar"] {{ background:var(--surface) !important; border-right:1px solid var(--border); }}

        /* ---- Metric ---- */
        [data-testid="stMetric"] {{ background:var(--surface2); border:1px solid var(--border);
            border-radius:14px; padding:10px 14px; }}

        /* expander */
        [data-testid="stExpander"] {{ border-radius:14px; border:1px solid var(--border); overflow:hidden; }}
        </style>
        """,
        unsafe_allow_html=True,
    )


_theme_mode = st.session_state.get("theme_choice", "Light")
inject_theme(_theme_mode)

# ----------------------------------------------------------------------------
# LOAD DATA (single source of truth, reloaded each run)
# ----------------------------------------------------------------------------
data = load_data()

# Apply-tab draft state (kept in session, not persisted until "Submitted")
ss = st.session_state
ss.setdefault("apply_match", None)
ss.setdefault("apply_resume_bytes", None)
ss.setdefault("apply_resume_content", None)
ss.setdefault("apply_resume_downloaded", False)
ss.setdefault("job_results", None)
ss.setdefault("job_errors", None)
ss.setdefault("job_fallback", False)

# Hand-off from the Job Matches tab: a chosen JD is injected into the Apply tab here,
# BEFORE the Apply text area is created, so the widget picks it up cleanly.
if ss.get("pending_jd") is not None:
    ss["apply_jd"] = ss.pop("pending_jd")
    ss["_jd_loaded"] = True


# ----------------------------------------------------------------------------
# HEADER + CLEAR ALL DATA
# ----------------------------------------------------------------------------
@st.dialog("Clear All Data?")
def confirm_clear():
    st.warning("This will permanently wipe your profile and applications from this session "
               "and reset all tabs. Download your data first if you want to keep it.")
    c1, c2 = st.columns(2)
    if c1.button("OK, clear everything", use_container_width=True, type="primary"):
        clear_all_data()
        ss.apply_match = None
        ss.apply_resume_bytes = None
        ss.apply_resume_downloaded = False
        st.rerun()
    if c2.button("Cancel", use_container_width=True):
        st.rerun()


st.markdown(
    """
    <div class="hero">
        <h1>🧭 AI Job Application Assistant</h1>
        <p>Your AI wingman for landing the role — tailor resumes, match skills, track apps &amp; learn from every no.</p>
        <div class="chips">
            <span class="chip">✨ Smart skill match</span>
            <span class="chip">📄 Auto-tailored resume</span>
            <span class="chip">📊 Live tracker</span>
            <span class="chip">🧠 Learns from rejections</span>
        </div>
    </div>
    """,
    unsafe_allow_html=True,
)


# ----------------------------------------------------------------------------
# SIDEBAR: SETTINGS + LIVING PROFILE STATE
# ----------------------------------------------------------------------------
def _remember(widget_key, store_key):
    """Copy a widget's value into a plain session dict. Streamlit discards the state
    of widgets that aren't drawn, so this keeps each provider's key and model when
    the user switches provider and back."""
    ss.setdefault(store_key, {})[widget_key] = ss.get(widget_key, "")


with st.sidebar:
    st.markdown("### 🔑 AI Provider")
    # Locally, an API.txt (OpenAI) key makes OpenAI the default; otherwise Gemini (free).
    ss.setdefault(PROVIDER_CHOICE_KEY, "openai" if _local_api_key() else DEFAULT_PROVIDER)
    provider_id = st.selectbox(
        "Provider", list(PROVIDERS), key=PROVIDER_CHOICE_KEY,
        format_func=lambda p: PROVIDERS[p]["label"],
    )
    pcfg = PROVIDERS[provider_id]

    key_widget = api_key_state_key(provider_id)
    model_widget = model_state_key(provider_id)
    if key_widget not in ss:
        ss[key_widget] = ss.get("_saved_api_keys", {}).get(key_widget, "")
    if model_widget not in ss:
        ss[model_widget] = ss.get("_saved_models", {}).get(model_widget, pcfg["chat_model"])

    st.text_input(
        f"{pcfg['name']} API key", type="password", key=key_widget,
        placeholder=f"{pcfg['key_prefix']}...",
        on_change=_remember, args=(key_widget, "_saved_api_keys"),
        help="Used only for your own requests during this session. It is never "
             "saved to disk or shared with other users.",
    )
    typed_key = ss.get(key_widget, "").strip()
    api_key = typed_key or (_local_api_key() if provider_id == "openai" else "")
    if not api_key:
        st.info(
            "Add an API key to use the AI features. "
            f"**Free options:** [Google Gemini ↗]({PROVIDERS['gemini']['key_url']}) and "
            f"[Groq ↗]({PROVIDERS['groq']['key_url']}) (rate-limited). "
            "OpenAI and xAI Grok are paid."
        )
    elif not api_key.startswith(pcfg["key_prefix"]):
        st.warning(f"That doesn't look like a {pcfg['name']} API key (they start with "
                   f"`{pcfg['key_prefix']}`).")
    if typed_key:
        st.caption("Kept only in this browser session — never stored on the server or "
                   "shared. Usage is billed to your own account with the provider.")
    elif api_key:
        st.caption("Using the local API.txt key.")
    st.caption(f"[Get your {pcfg['name']} API key ↗]({pcfg['key_url']})")
    with st.expander("Model"):
        st.text_input(
            "Model name", key=model_widget,
            on_change=_remember, args=(model_widget, "_saved_models"),
            help=f"Default: {pcfg['chat_model']}. Change it if {pcfg['name']} renames "
                 "or retires the model, or to use a different one.",
        )
        if not pcfg.get("embed_model"):
            st.caption(f"{pcfg['name']} has no embeddings API, so skill matching uses "
                       "local text similarity instead of meaning-based similarity.")
    st.markdown("---")

    st.markdown("### ⚙️ Settings")
    st.radio("Appearance", ["Light", "Dark"], key="theme_choice", horizontal=True)
    if st.button("🗑️ Clear All Data", use_container_width=True):
        confirm_clear()
    st.markdown("---")

    st.markdown("### 💾 Your Data")
    st.caption("Your workspace lives only in this browser session and is lost when "
               "you close or refresh the page. Download it to continue later.")
    st.download_button(
        "⬇️ Download my data", data=export_json(data),
        file_name=f"job_assistant_data_{datetime.now():%Y%m%d}.json",
        mime="application/json", use_container_width=True,
    )
    restore_file = st.file_uploader("Restore saved data (.json)", type=["json"],
                                    key="restore_uploader")
    if restore_file is not None:
        sig = f"{restore_file.name}:{restore_file.size}"
        if ss.get("restore_sig") != sig:
            try:
                data = import_json(restore_file.getvalue())
                ss.restore_sig = sig
                ss.apply_match = None
                ss.apply_resume_bytes = None
                ss.apply_resume_downloaded = False
                st.rerun()
            except ValueError as e:
                st.error(str(e))
        else:
            st.success("Saved data restored.")
    st.markdown("---")

    st.markdown("### 👤 Living Candidate Profile")
    cps = data.get("current_profile_state")
    if not data.get("profile_uploaded") or not cps:
        st.info("No profile loaded yet. Upload one in the Apply tab.")
    else:
        st.markdown(f"**Experience level:** {cps.get('experience_level','-')}")
        st.markdown("**Skills (current levels):**")
        for sk in cps.get("skills", []):
            lvl = sk.get("level", "moderate")
            cls = {"strong": "pill-strong", "moderate": "pill-mod", "weak": "pill-miss"}.get(lvl, "pill-mod")
            st.markdown(
                f"<span class='pill {cls}'>{sk.get('name','')} · {lvl}</span>",
                unsafe_allow_html=True,
            )
        if cps.get("learning_areas"):
            st.markdown("**Learning areas (from rejections):**")
            for la in cps["learning_areas"]:
                st.markdown(f"- {la}")
        with st.expander("Profile change history"):
            for h in reversed(data.get("profile_history", [])):
                st.markdown(f"- _{h['timestamp']}_ — {h['change']}")


# ----------------------------------------------------------------------------
# API KEY CHECK — each user brings their own key (entered in the sidebar). The app
# opens normally without one; AI actions point the user to the sidebar instead.
# ----------------------------------------------------------------------------
def _require_key():
    """True if an API key is set; otherwise shows a short pointer to the sidebar."""
    if api_key:
        return True
    st.warning("🔑 Add your AI provider API key in the **sidebar** to use this "
               "(open it with the **›** arrow at the top-left). Google Gemini and Groq "
               "offer free keys.")
    return False


# ----------------------------------------------------------------------------
# TABS
# ----------------------------------------------------------------------------
tab_apply, tab_jobs, tab_track, tab_reject, tab_global, tab_eval = st.tabs(
    ["📝 Apply", "🔎 Job Matches", "📊 Tracking", "❌ Not Selected",
     "🌐 Global Analysis", "📈 Evaluation"]
)


# ============================================================================
# TAB 1 — APPLY
# ============================================================================
with tab_apply:
    st.subheader("1 · Inputs")

    col1, col2 = st.columns(2)
    with col1:
        profile_file = st.file_uploader(
            "Candidate Profile (.txt, .pdf, .docx)",
            type=["txt", "pdf", "docx"], key="profile_uploader",
        )
        if profile_file is not None and _require_key():
            sig = f"{profile_file.name}:{profile_file.size}"
            if ss.get("profile_sig") != sig:
                with st.spinner("Extracting and analysing profile..."):
                    try:
                        text = extract_text_from_upload(profile_file.name, profile_file.getvalue())
                        state = analyze_profile(api_key, text)
                        set_initial_profile(data, state, text, _now())
                        ss.profile_sig = sig
                        st.rerun()
                    except Exception as e:
                        st.error(f"Profile error: {e}")
        if data.get("profile_uploaded"):
            st.markdown("<span class='ok-badge'>✅ Profile Loaded</span>", unsafe_allow_html=True)
            with st.expander("Extracted profile text preview"):
                st.text((data.get("profile_raw_text", "") or "")[:2000])

    with col2:
        ref_file = st.file_uploader(
            "Reference Resume — optional (.pdf, .docx)",
            type=["pdf", "docx"], key="ref_uploader",
        )
        if ref_file is not None and _require_key():
            sig = f"{ref_file.name}:{ref_file.size}"
            if ss.get("ref_sig") != sig:
                with st.spinner("Extracting reference resume style & colours..."):
                    try:
                        raw = ref_file.getvalue()
                        text = extract_text_from_upload(ref_file.name, raw)
                        style = extract_reference_style(api_key, text) or {}
                        colors = extract_colors_from_upload(ref_file.name, raw)
                        if colors:
                            style["colors"] = colors
                        data["reference_style"] = style
                        save_data(data)
                        ss.ref_sig = sig
                        st.rerun()
                    except Exception as e:
                        st.error(f"Reference resume error: {e}")
        if data.get("reference_style"):
            st.markdown("<span class='ok-badge'>✅ Reference Resume Loaded</span>", unsafe_allow_html=True)
            rs = data.get("reference_style") or {}
            ref_colors = rs.get("colors")
            if ref_colors and ref_colors.get("accent"):
                st.markdown(
                    "Detected template colour: "
                    f"<span style='display:inline-block;width:14px;height:14px;border-radius:4px;"
                    f"vertical-align:middle;border:1px solid #ccc;background:{ref_colors['accent']}'></span> "
                    f"<code>{ref_colors['accent']}</code>",
                    unsafe_allow_html=True,
                )
            with st.expander("Detected format (the generated resume will match this)"):
                st.markdown(
                    f"- **Section order:** {', '.join(rs.get('section_order', [])) or 'default'}\n"
                    f"- **Density:** {rs.get('density', 'detailed')}\n"
                    f"- **Header alignment:** {rs.get('header_style', 'left')}\n"
                    f"- **Bullet style:** {rs.get('bullet_style', 'bullets')}\n"
                    f"- **Tone:** {rs.get('tone', 'formal')}"
                )
                st.caption("Format/structure only — your reference resume's content is never copied.")
        else:
            st.caption("No reference resume — a default professional template is used.")

    if ss.pop("_jd_loaded", False):
        st.success("Loaded a job description from **Job Matches** below. "
                   "Click **Extract Fields & Run Skill Match** to continue.")
    jd_text = st.text_area(
        "Job Description (paste from LinkedIn, portal, email, etc.)",
        key="apply_jd", height=180,
    )

    run_match = st.button(
        "🔎 Extract Fields & Run Skill Match",
        disabled=not (data.get("profile_uploaded") and jd_text.strip()),
        help="Requires a loaded profile and a pasted job description.",
    )
    if run_match and _require_key():
        with st.spinner("Analysing JD and matching against the living profile..."):
            try:
                # Two separate, minimal-context calls (per the AI guardrails):
                # 1) JD analysis (JD only) -> 2) skill match (profile + JD JSON).
                jd_json = analyze_jd(api_key, jd_text)
                match_result = match_skills(api_key, jd_json, data["current_profile_state"])
                match = {**jd_json, **match_result}  # combined view for the UI
                ss.apply_match = match
                ss.apply_resume_bytes = None
                ss.apply_resume_downloaded = False
                ss["apply_company"] = match.get("company_name", "")
                ss["apply_role"] = match.get("job_role", "")
                ss["apply_skills_required"] = ", ".join(match.get("required_skills", []))
                st.rerun()
            except Exception as e:
                st.error(f"⚠️ {e}")

    # ---- Required fields after input ----
    match = ss.apply_match
    if match:
        st.subheader("2 · Extracted Fields & Skill Match")
        fc1, fc2 = st.columns(2)
        with fc1:
            st.text_input("Company Name", key="apply_company")
            st.text_input("Job Role", key="apply_role")
            st.text_area("Skills Required (editable, comma-separated)",
                         key="apply_skills_required", height=80)
        with fc2:
            st.markdown("**User Profile Skills (current state):**")
            st.markdown(
                " ".join(
                    f"<span class='pill pill-strong'>{s['name']}</span>"
                    for s in data["current_profile_state"].get("skills", [])
                ) or "_none_",
                unsafe_allow_html=True,
            )
            st.metric("Skill Match", f"{match['match_percentage']}%")
            st.caption("AI-generated estimate")

        st.markdown("**Match breakdown**")
        b1, b2, b3 = st.columns(3)
        b1.markdown("**🟢 Strong Match**<br>" + ("<br>".join(match.get("strong_match", [])) or "—"), unsafe_allow_html=True)
        b2.markdown("**🟡 Partial Match**<br>" + ("<br>".join(match.get("partial_match", [])) or "—"), unsafe_allow_html=True)
        b3.markdown("**🔴 Missing Skill**<br>" + ("<br>".join(match.get("missing_skills", [])) or "—"), unsafe_allow_html=True)
        explanation = match.get("match_explanation") or match.get("summary")
        if explanation:
            st.caption("Reasoning: " + explanation)

        # ---- Button activation rules ----
        company = ss.get("apply_company", "").strip()
        role = ss.get("apply_role", "").strip()
        skills_req = [s.strip() for s in ss.get("apply_skills_required", "").split(",") if s.strip()]
        profile_skills = data["current_profile_state"].get("skills", [])

        conditions = {
            "Candidate profile uploaded": data.get("profile_uploaded"),
            "JD text pasted": bool(jd_text.strip()),
            "Company Name filled": bool(company),
            "Job Role filled": bool(role),
            "Skills Required extracted": len(skills_req) > 0,
            "User Profile Skills extracted": len(profile_skills) > 0,
            "Skill Match calculated": match is not None,
        }
        all_ready = all(conditions.values())

        st.subheader("3 · Resume")
        with st.expander("Resume readiness checklist", expanded=not all_ready):
            for label, ok in conditions.items():
                st.markdown(f"{'✅' if ok else '⬜'} {label}")

        if st.button("📄 Prepare Resume", disabled=not all_ready) and _require_key():
            with st.spinner("Generating a detailed, ATS-friendly resume with ReportLab..."):
                try:
                    ref_style = data.get("reference_style") or {}
                    jd_summary = {
                        "job_role": match.get("job_role", role),
                        "required_skills": skills_req or match.get("required_skills", []),
                        "responsibilities": match.get("responsibilities", []),
                        "seniority": match.get("seniority", ""),
                    }
                    content = write_resume_content(
                        api_key, data["current_profile_state"], jd_summary, ref_style,
                    )
                    ref_colors = ref_style.get("colors")
                    header_align = ref_style.get("header_style", "left")
                    ss.apply_resume_bytes = generate_resume_pdf(
                        content, ref_colors, header_align)
                    ss.apply_resume_content = content  # for the Evaluation tab
                    ss.apply_resume_downloaded = False
                    st.success("Resume generated. Download it below.")
                except Exception as e:
                    st.error(f"⚠️ {e}")

        if ss.apply_resume_bytes:
            fname = f"resume_{(company or 'company').replace(' ', '_')}.pdf"
            if st.download_button("⬇️ Download Resume (PDF)", data=ss.apply_resume_bytes,
                                  file_name=fname, mime="application/pdf"):
                ss.apply_resume_downloaded = True

            if ss.apply_resume_downloaded:
                st.info("Now apply to the company offline, then click **Submitted** to log it.")

            # ---- Submitted button ----
            submitted_ready = ss.apply_resume_downloaded and all_ready
            if st.button("✅ Submitted (log application)", disabled=not submitted_ready,
                         help="Enabled after the resume has been generated and downloaded."):
                app_obj = {
                    "id": uuid.uuid4().hex[:8],
                    "company_name": company,
                    "job_role": role,
                    "skills_required": skills_req,
                    "match_percentage": match["match_percentage"],
                    "strong_match": match.get("strong_match", []),
                    "partial_match": match.get("partial_match", []),
                    "missing_skills": match.get("missing_skills", []),
                    "jd": jd_text,
                    "status": "Submitted",
                    "status_history": [{"status": "Submitted", "timestamp": _now()}],
                    "rejection_notes": "",
                    "rejection_analysis": None,
                    "resume_generated": True,
                }
                add_application(data, app_obj)
                ss.apply_match = None
                ss.apply_resume_bytes = None
                ss.apply_resume_downloaded = False
                st.success(f"Application to {company} logged in the Tracking tab.")
                st.rerun()


# ============================================================================
# TAB 2 — TRACKING
# ============================================================================
@st.dialog("Mark as Selected directly?")
def confirm_selected(app_id):
    st.write("Candidate has not been marked for **Interview**. "
             "Do you want to mark as **Selected** directly?")
    c1, c2 = st.columns(2)
    if c1.button("OK", use_container_width=True, type="primary"):
        set_status(data, app_id, "Selected", _now())
        st.rerun()
    if c2.button("Cancel", use_container_width=True):
        st.rerun()


@st.dialog("Mark as Not Selected?")
def confirm_not_selected(app_id):
    st.write("Are you sure you want to mark this application as **Not Selected**? "
             "This will move it to the Not Selected tab for rejection analysis.")
    c1, c2 = st.columns(2)
    if c1.button("OK", use_container_width=True, type="primary"):
        set_status(data, app_id, "Not Selected", _now())
        st.rerun()
    if c2.button("Cancel", use_container_width=True):
        st.rerun()


with tab_track:
    st.subheader("Active Applications")
    actives = active_applications(data)
    if not actives:
        st.info("No active applications yet. Submit one from the Apply tab.")
    else:
        for app in actives:
            with st.container():
                st.markdown("<div class='card'>", unsafe_allow_html=True)
                top = st.columns([3, 2, 2, 2])
                top[0].markdown(f"**{app['company_name']}**  \n{app['job_role']}")
                top[1].markdown(f"Match  \n**{app['match_percentage']}%**")
                status = app.get("status", "Submitted")
                if status == "Submitted":
                    top[2].markdown(
                        "<span class='blink-dot'></span><span class='status-submitted'>Submitted</span>",
                        unsafe_allow_html=True,
                    )
                else:
                    top[2].markdown(f"Status  \n**{status}**")
                with top[3].popover("View JD"):
                    st.write(app.get("jd", ""))

                if app.get("skills_required"):
                    st.caption("Skills required: " + ", ".join(app["skills_required"]))

                b = st.columns(4)
                # Submitted (already set automatically; shown disabled as a state marker)
                b[0].button("Submitted", key=f"sub_{app['id']}", disabled=True)
                # Interview — clickable once Submitted
                if b[1].button("Interview", key=f"int_{app['id']}",
                               disabled=status not in ("Submitted",)):
                    set_status(data, app["id"], "Interview", _now())
                    st.rerun()
                # Selected — confirm if Interview not done
                if b[2].button("Selected", key=f"sel_{app['id']}",
                               disabled=status == "Selected"):
                    if status == "Interview":
                        set_status(data, app["id"], "Selected", _now())
                        st.rerun()
                    else:
                        confirm_selected(app["id"])
                # Not Selected — always active
                if b[3].button("Not Selected", key=f"rej_{app['id']}"):
                    confirm_not_selected(app["id"])
                st.markdown("</div>", unsafe_allow_html=True)


# ============================================================================
# TAB 3 — NOT SELECTED
# ============================================================================
with tab_reject:
    st.subheader("Rejection Analysis & Profile Learning")
    rejects = rejected_applications(data)
    if not rejects:
        st.info("No rejected applications yet. When you mark an application "
                "'Not Selected' in Tracking, it appears here for analysis.")
    else:
        for app in rejects:
            with st.container():
                st.markdown("<div class='card'>", unsafe_allow_html=True)
                hc = st.columns([3, 2])
                hc[0].markdown(f"**{app['company_name']}** — {app['job_role']}")
                hc[1].markdown(f"Experience Match: **{app['match_percentage']}%**")

                notes = st.text_area(
                    "Rejection notes (interview questions you couldn't answer, "
                    "topics you felt weak in, the rejection email, recruiter feedback, "
                    "personal observations — write freely)",
                    value=app.get("rejection_notes", ""),
                    key=f"notes_{app['id']}", height=130,
                )

                if st.button("🔬 Analyze", key=f"an_{app['id']}") and _require_key():
                    with st.spinner("Analysing rejection and updating the living profile..."):
                        try:
                            result = analyze_rejection(
                                api_key, notes, app, data["current_profile_state"]
                            )
                            # Persist notes + analysis, then evolve the profile
                            get_application(data, app["id"])
                            app["rejection_notes"] = notes
                            app["rejection_analysis"] = result
                            save_data(data)
                            apply_profile_update(data, result, app["company_name"], _now())
                            st.rerun()
                        except Exception as e:
                            st.error(f"⚠️ {e}")

                if app.get("rejection_analysis"):
                    ra = app["rejection_analysis"]
                    st.markdown("**Analysis summary:** " + ra.get("analysis_summary", ""))
                    cc = st.columns(3)
                    cc[0].markdown("**Skill gaps**<br>" + ("<br>".join(ra.get("skill_gaps", [])) or "—"),
                                   unsafe_allow_html=True)
                    cc[1].markdown("**Struggled topics**<br>" + ("<br>".join(ra.get("struggled_topics", [])) or "—"),
                                   unsafe_allow_html=True)
                    cc[2].markdown("**Weaker experience**<br>" + ("<br>".join(ra.get("weaker_experience_areas", [])) or "—"),
                                   unsafe_allow_html=True)
                    if ra.get("new_learning_areas"):
                        st.caption("New learning areas added to profile: " +
                                   ", ".join(ra["new_learning_areas"]))
                st.markdown("</div>", unsafe_allow_html=True)


# ============================================================================
# TAB 4 — GLOBAL ANALYSIS
# ============================================================================
with tab_global:
    st.subheader("Consolidated Patterns Across All Rejections")
    analyzed_rejects = [a for a in rejected_applications(data) if a.get("rejection_analysis")]

    if len(analyzed_rejects) < 1:
        st.info("Not enough rejection data available.")
    else:
        st.caption(f"Reading {len(analyzed_rejects)} analysed rejection"
                   f"{'s' if len(analyzed_rejects) != 1 else ''} into one consolidated report.")
        if st.button("🌐 Run Global Analysis") and _require_key():
            with st.spinner("Generating one consolidated report across all rejections..."):
                try:
                    result = run_global_analysis(api_key, analyzed_rejects)
                    data["global_analysis"] = result
                    save_data(data)
                    st.rerun()
                except Exception as e:
                    st.error(f"⚠️ {e}")

        ga = data.get("global_analysis")
        if ga:
            st.markdown("### 🔁 Recurring Skill Gaps")
            st.markdown("\n".join(f"- {x}" for x in ga.get("recurring_skill_gaps", [])) or "_None identified_")
            st.markdown("### ❓ Frequently Asked Topics")
            st.markdown("\n".join(f"- {x}" for x in ga.get("frequently_asked_topics", [])) or "_None identified_")
            st.markdown("### 📉 Weak Domains")
            st.markdown("\n".join(f"- {x}" for x in ga.get("weak_domains", [])) or "_None identified_")
            st.markdown("### 🗺️ Learning Roadmap")
            st.markdown(ga.get("learning_roadmap", "") or "_No roadmap generated._")
            st.markdown("### ✅ Final Recommendation")
            st.success(ga.get("final_recommendation", "") or "No recommendation generated.")
        else:
            st.caption("Click **Run Global Analysis** to generate the consolidated report.")


# ============================================================================
# TAB — JOB MATCHES  (live jobs fetched and ranked against the resume)
# ============================================================================
with tab_jobs:
    st.subheader("🔎 Jobs Matched to Your Resume")
    if not data.get("profile_uploaded"):
        st.info("Upload your candidate profile in the **Apply** tab first — then this "
                "tab fetches live jobs and ranks them against your resume.")
    else:
        cps = data["current_profile_state"]
        if "job_query" not in ss:
            roles = [e.get("role") or e.get("title") for e in cps.get("experience", [])
                     if isinstance(e, dict)]
            top_skills = [s.get("name") for s in cps.get("skills", [])[:3] if s.get("name")]
            ss["job_query"] = (roles[0] if roles and roles[0] else " ".join(top_skills)) \
                or "software engineer"

        c1, c2 = st.columns([3, 2])
        query = c1.text_input("Search roles / skills", key="job_query")
        sources = c2.multiselect("Sources", list(PROVIDERS.keys()),
                                 default=list(PROVIDERS.keys()), key="job_sources")
        n = st.slider("Results per source", 5, 20, 10, key="job_n")

        if st.button("🔍 Search Jobs", type="primary") and _require_key():
            with st.spinner("Fetching live jobs and ranking them against your resume..."):
                try:
                    jobs, errors, fallback = search_jobs(query, sources or None, n)
                    ss.job_results = rank_jobs(jobs, cps, api_key)
                    ss.job_errors = errors
                    ss.job_fallback = fallback
                except Exception as e:
                    st.error(f"⚠️ {e}")

        if ss.job_fallback:
            st.warning("Live sources were unreachable or returned nothing — showing "
                       "sample postings so you can still try the feature.")
        if ss.job_errors:
            with st.expander("Source notes"):
                for er in ss.job_errors:
                    st.caption(er)

        jobs = ss.job_results
        if jobs:
            st.caption(f"{len(jobs)} jobs ranked by resume fit (skill overlap + semantic similarity).")
            for i, j in enumerate(jobs):
                st.markdown("<div class='card'>", unsafe_allow_html=True)
                top = st.columns([5, 2, 2])
                top[0].markdown(f"**{j['title']}**  \n{j['company']} · {j['location']}")
                fit = j.get("fit")
                top[1].markdown(f"Resume fit  \n**{fit}%**" if fit is not None else "Resume fit  \n—")
                top[2].markdown(f"<span class='pill pill-strong'>{j['source']}</span>",
                                unsafe_allow_html=True)
                if j.get("matched_skills"):
                    st.caption("Matches your skills: " + ", ".join(j["matched_skills"]))
                with st.expander("View job description"):
                    st.write(j.get("description") or "—")
                    if j.get("url"):
                        st.markdown(f"[↗ Open original posting]({j['url']})")
                if st.button("📌 Use this JD in Apply", key=f"usejd_{i}"):
                    ss["pending_jd"] = j.get("description", "")
                    st.rerun()
                st.markdown("</div>", unsafe_allow_html=True)
        elif jobs is not None:
            st.info("No matching jobs found — try a broader search term.")

        st.caption("Live sources: public job-board feeds (Remotive, Arbeitnow, RemoteOK). "
                   "Portals like AmbitionBox / Naukri / LinkedIn block automated access and "
                   "their terms prohibit scraping, so they are not queried directly.")


# ============================================================================
# TAB — EVALUATION  (output-quality + performance metrics on live session data)
# ============================================================================
with tab_eval:
    st.subheader("📈 Evaluation Metrics")
    st.caption("Computed on this session's real outputs — no extra AI calls.")

    match = ss.get("apply_match")
    resume = ss.get("apply_resume_content")

    # ---------- Output quality ----------
    st.markdown("### Output Quality")
    if not (match and resume):
        st.info("Run a skill match and **Prepare Resume** in the Apply tab to populate "
                "quality metrics (groundedness, ATS coverage, guardrails).")
    else:
        profile_state = data.get("current_profile_state") or {}
        raw_text = data.get("profile_raw_text", "")
        g_pct, ungrounded = evaluation.groundedness(resume, profile_state, raw_text)
        ats_pct, ats_missing = evaluation.ats_coverage(resume, match.get("required_skills", []))
        cov_pct = evaluation.skill_match_coverage(match)
        checks = evaluation.guardrail_checks(resume, match)
        guard_pct = round(100 * sum(1 for _, ok in checks if ok) / len(checks)) if checks else None
        overall = evaluation.overall_score([g_pct, ats_pct, cov_pct, guard_pct])

        m = st.columns(4)
        m[0].metric("Groundedness", f"{g_pct}%" if g_pct is not None else "—",
                    help="Share of resume skills traceable to the real profile (anti-hallucination).")
        m[1].metric("ATS Keyword Coverage", f"{ats_pct}%" if ats_pct is not None else "—",
                    help="Share of the JD's required skills that appear in the resume.")
        m[2].metric("Skill-Match Coverage", f"{cov_pct}%" if cov_pct is not None else "—",
                    help="Share of required skills the candidate already has (strong or partial).")
        m[3].metric("Guardrail Pass Rate", f"{guard_pct}%" if guard_pct is not None else "—",
                    help="Hard AI guardrails satisfied by the outputs.")

        if overall is not None:
            st.progress(overall / 100, text=f"Overall quality score: {overall}%")

        st.markdown("#### Guardrail compliance")
        for label, ok in checks:
            st.markdown(f"{'✅' if ok else '❌'} {label}")
        if ungrounded:
            st.caption("⚠️ Resume skills not found in the profile: " + ", ".join(ungrounded[:8]))
        if ats_missing:
            st.caption("JD skills not yet reflected in the resume: " + ", ".join(ats_missing[:8]))

    # ---------- Performance ----------
    st.markdown("### Performance & Cost")
    metrics = get_metrics()
    if not metrics:
        st.info("No AI calls recorded yet this session.")
    else:
        agg = {}
        for r in metrics:
            a = agg.setdefault(r["label"], {"calls": 0, "lat": 0.0, "pt": 0, "ct": 0, "model": r["model"]})
            a["calls"] += 1
            a["lat"] += r["latency"]
            a["pt"] += r["prompt_tokens"]
            a["ct"] += r["completion_tokens"]

        total_calls = sum(a["calls"] for a in agg.values())
        total_tokens = sum(a["pt"] + a["ct"] for a in agg.values())
        total_lat = sum(a["lat"] for a in agg.values())
        total_cost = sum(estimate_cost(a["pt"], a["ct"], a["model"]) for a in agg.values())

        p = st.columns(4)
        p[0].metric("AI calls", total_calls)
        p[1].metric("Total tokens", f"{total_tokens:,}")
        p[2].metric("Total LLM time", f"{total_lat:.1f}s")
        p[3].metric("Est. cost", f"${total_cost:.4f}")

        rows = []
        for label, a in sorted(agg.items(), key=lambda x: -x[1]["calls"]):
            rows.append({
                "Agent": label,
                "Calls": a["calls"],
                "Avg latency (s)": round(a["lat"] / a["calls"], 2),
                "Prompt tok": a["pt"],
                "Output tok": a["ct"],
                "Est. $": round(estimate_cost(a["pt"], a["ct"], a["model"]), 5),
            })
        st.dataframe(rows, use_container_width=True, hide_index=True)
        st.caption("Cost is an approximate estimate at current public gpt-4o / "
                   "text-embedding-3-small rates; other models (e.g. free-tier Gemini "
                   "or Groq) show $0. Token counts are exact (from the API).")
        if st.button("Reset performance metrics"):
            reset_metrics()
            st.rerun()
