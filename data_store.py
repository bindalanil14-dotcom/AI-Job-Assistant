"""
Per-user JSON data store for the AI Job Application Assistant.

Everything the app needs lives in ONE JSON document per user:
- current_profile_state : the evolving candidate profile (the living document)
- profile_history       : what changed after each rejection analysis
- applications          : every application with status history, notes, analysis

The document is kept in the visitor's own Streamlit session, so every user of a
deployed app has a private workspace. Users save it with export_json() (a
downloadable .json file) and bring it back next time with import_json().
No database is used by design.
"""

import json

import streamlit as st

SESSION_KEY = "user_data"


def _empty_state():
    return {
        "current_profile_state": None,   # set when a profile is first uploaded
        "profile_uploaded": False,
        "profile_raw_text": "",
        "reference_style": None,         # style guide extracted from reference resume
        "profile_history": [],           # list of {timestamp, change, details}
        "applications": [],              # list of application objects
        "global_analysis": None,         # last computed Tab-4 result
    }


def _backfill(data):
    """Add any missing top-level keys (forward compatibility)."""
    for k, v in _empty_state().items():
        data.setdefault(k, v)
    return data


def load_data():
    """Return this user's store, creating an empty one on their first visit."""
    if SESSION_KEY not in st.session_state:
        st.session_state[SESSION_KEY] = _empty_state()
    return _backfill(st.session_state[SESSION_KEY])


def save_data(data):
    st.session_state[SESSION_KEY] = data


def clear_all_data():
    """Wipe this user's store completely back to the empty state."""
    data = _empty_state()
    save_data(data)
    return data


def export_json(data):
    """Serialise the store for the 'Download my data' button."""
    return json.dumps(data, indent=2, ensure_ascii=False).encode("utf-8")


def import_json(raw_bytes):
    """Replace this user's store with a previously downloaded file.
    Raises ValueError if the file is not a valid saved workspace."""
    try:
        data = json.loads(raw_bytes.decode("utf-8"))
    except Exception as e:  # noqa: BLE001
        raise ValueError("That file is not valid JSON.") from e
    if not isinstance(data, dict) or not isinstance(data.get("applications", []), list):
        raise ValueError("That file is not a saved workspace from this app.")
    data = _backfill(data)
    save_data(data)
    return data


# ----------------------------------------------------------------------------
# PROFILE STATE (the living document)
# ----------------------------------------------------------------------------
def set_initial_profile(data, profile_state, raw_text, timestamp):
    """Install the first profile state from the uploaded document analysis."""
    data["current_profile_state"] = profile_state
    data["profile_uploaded"] = True
    data["profile_raw_text"] = raw_text
    data["profile_history"].append({
        "timestamp": timestamp,
        "change": "Initial profile loaded from uploaded document.",
        "details": {
            "skills": [s["name"] for s in profile_state.get("skills", [])],
            "experience_level": profile_state.get("experience_level"),
        },
    })
    save_data(data)
    return data


def apply_profile_update(data, rejection_result, company, timestamp):
    """
    Evolve current_profile_state using a rejection analysis result:
    - downgrade named skills to their new level
    - append new learning areas
    - record the change in profile_history
    """
    state = data.get("current_profile_state") or {
        "skills": [], "tools": [], "experience_level": "junior",
        "projects": [], "domains": [], "learning_areas": [],
    }
    skills = state.setdefault("skills", [])
    name_index = {s["name"].lower(): s for s in skills}

    applied_downgrades = []
    for dg in rejection_result.get("downgrades", []):
        name = dg.get("name", "")
        new_level = dg.get("new_level", "weak")
        existing = name_index.get(name.lower())
        if existing:
            old = existing.get("level")
            # only ever move downward: strong > moderate > weak
            order = {"strong": 3, "moderate": 2, "weak": 1}
            if order.get(new_level, 1) < order.get(old, 2):
                existing["level"] = new_level
                applied_downgrades.append(f"{name}: {old} -> {new_level}")
        else:
            # skill surfaced by the rejection but absent from profile: add it weak
            skills.append({"name": name, "level": new_level})
            applied_downgrades.append(f"{name}: added as {new_level}")

    learning = state.setdefault("learning_areas", [])
    added_learning = []
    for area in rejection_result.get("new_learning_areas", []):
        if area and area not in learning:
            learning.append(area)
            added_learning.append(area)

    data["current_profile_state"] = state
    data["profile_history"].append({
        "timestamp": timestamp,
        "change": f"Profile updated after rejection from {company}.",
        "details": {
            "downgrades": applied_downgrades,
            "new_learning_areas": added_learning,
            "analysis_summary": rejection_result.get("analysis_summary", ""),
        },
    })
    save_data(data)
    return data


# ----------------------------------------------------------------------------
# APPLICATIONS
# ----------------------------------------------------------------------------
def add_application(data, app_obj):
    data["applications"].append(app_obj)
    save_data(data)
    return data


def get_application(data, app_id):
    for app in data["applications"]:
        if app["id"] == app_id:
            return app
    return None


def update_application(data, app_id, **fields):
    app = get_application(data, app_id)
    if app:
        app.update(fields)
        save_data(data)
    return app


def set_status(data, app_id, status, timestamp):
    app = get_application(data, app_id)
    if app:
        app["status"] = status
        app.setdefault("status_history", []).append(
            {"status": status, "timestamp": timestamp}
        )
        save_data(data)
    return app


def active_applications(data):
    """Applications still in the tracking pipeline (not rejected)."""
    return [a for a in data["applications"] if a.get("status") != "Not Selected"]


def rejected_applications(data):
    return [a for a in data["applications"] if a.get("status") == "Not Selected"]
