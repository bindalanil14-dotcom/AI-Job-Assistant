"""
Modular LLM provider layer.
=================================================================
The rest of the app talks to this layer, never to a vendor SDK directly.
Every supported provider (OpenAI, Google Gemini, Groq, xAI Grok) exposes an
OpenAI-compatible API, so one class drives them all; each is described by an
entry in PROVIDERS. To add another OpenAI-compatible provider, add an entry —
no other file changes. Prompts live in skills/*.md (centralised); models/params
live here.

The user picks a provider and pastes their key in the sidebar; get_provider()
reads that choice from their session.

Guardrails implemented here:
- Deterministic-where-possible: callers pass low temperatures.
- Error handling: every call retries ONCE automatically; on a second failure a
  user-friendly RuntimeError is raised (internal details are not surfaced).
- JSON-only responses for structured agents.
- Providers without an embeddings API fall back to a local text-similarity
  embedding, so skill matching and job ranking still work.
"""

import json
import re
import time
import zlib

import streamlit as st

# ----------------------------------------------------------------------------
# SUPPORTED PROVIDERS (all OpenAI-compatible)
#   token_multiplier: thinking/reasoning models spend part of max_tokens on
#   reasoning, so their output budget is raised to avoid truncated JSON.
# ----------------------------------------------------------------------------
PROVIDERS = {
    "gemini": {
        "label": "Google Gemini (free tier)",
        "name": "Gemini",
        "base_url": "https://generativelanguage.googleapis.com/v1beta/openai/",
        "chat_model": "gemini-3.8-flash",
        "embed_model": "gemini-embedding-001",
        "key_prefix": "AIza",
        "key_url": "https://aistudio.google.com/apikey",
        "token_multiplier": 4,
    },
    "groq": {
        "label": "Groq (free tier)",
        "name": "Groq",
        "base_url": "https://api.groq.com/openai/v1",
        "chat_model": "openai/gpt-oss-120b",
        "embed_model": None,
        "key_prefix": "gsk_",
        "key_url": "https://console.groq.com/keys",
        "token_multiplier": 4,
    },
    "openai": {
        "label": "OpenAI (paid)",
        "name": "OpenAI",
        "base_url": None,
        "chat_model": "gpt-4o",
        "embed_model": "text-embedding-3-small",
        "key_prefix": "sk-",
        "key_url": "https://platform.openai.com/api-keys",
        "token_multiplier": 1,
    },
    "xai": {
        "label": "xAI Grok (paid, trial credits)",
        "name": "xAI Grok",
        "base_url": "https://api.x.ai/v1",
        "chat_model": "grok-4.3",
        "embed_model": None,
        "key_prefix": "xai-",
        "key_url": "https://console.x.ai",
        "token_multiplier": 2,
    },
}
DEFAULT_PROVIDER = "gemini"

# Session-state keys shared with the sidebar in app.py.
PROVIDER_CHOICE_KEY = "llm_provider_id"


def model_state_key(provider_id):
    return f"llm_model_{provider_id}"


def api_key_state_key(provider_id):
    return f"user_api_key_{provider_id}"


# ----------------------------------------------------------------------------
# CALL METRICS (latency + token usage), used by the Evaluation tab.
# Kept in each user's session so users never see each other's usage.
# Approximate USD rates (per 1M tokens) — clearly an estimate, edit as needed.
# Models not listed here (e.g. free-tier ones) are counted as $0.
# ----------------------------------------------------------------------------
_METRICS_KEY = "llm_metrics"
RATES = {
    "gpt-4o": {"in": 2.50, "out": 10.00},
    "text-embedding-3-small": {"in": 0.02, "out": 0.0},
}


def _metrics():
    return st.session_state.setdefault(_METRICS_KEY, [])


def record_metric(label, kind, model, latency, prompt_tokens, completion_tokens):
    _metrics().append({
        "label": label, "kind": kind, "model": model,
        "latency": latency,
        "prompt_tokens": prompt_tokens or 0,
        "completion_tokens": completion_tokens or 0,
    })


def get_metrics():
    return list(_metrics())


def reset_metrics():
    _metrics().clear()


def estimate_cost(prompt_tokens, completion_tokens, model):
    r = RATES.get(model, {"in": 0.0, "out": 0.0})
    return prompt_tokens / 1e6 * r["in"] + completion_tokens / 1e6 * r["out"]


class LLMProvider:
    """Interface every provider must implement."""

    def chat_json(self, system, user, temperature=0.1, max_tokens=1500):
        raise NotImplementedError

    def embed(self, texts):
        raise NotImplementedError


# ----------------------------------------------------------------------------
# ERRORS
# ----------------------------------------------------------------------------
_UNAVAILABLE = ("The AI service is temporarily unavailable. Your data is safe — "
                "please try again.")


def _is_bad_key(err):
    """Invalid key. OpenAI/Groq send 401; Gemini and xAI send 400 with a key message."""
    import openai
    if isinstance(err, openai.AuthenticationError):
        return True
    msg = str(err).lower()
    return (isinstance(err, openai.BadRequestError) and "api key" in msg
            and any(w in msg for w in ("valid", "incorrect", "expired")))


def _is_key_problem(err):
    """Errors caused by the user's own key; retrying will not help."""
    import openai
    return _is_bad_key(err) or isinstance(err, openai.PermissionDeniedError)


def _friendly_error(err, name="OpenAI", model=""):
    """Map an API error to a message the user can act on (no internal details)."""
    import openai
    if _is_bad_key(err):
        return f"Your {name} API key was rejected. Check the key in the sidebar and try again."
    if isinstance(err, openai.PermissionDeniedError):
        return f"Your {name} API key does not have access to the model '{model}'."
    if isinstance(err, openai.NotFoundError):
        return (f"{name} does not recognise the model '{model}'. "
                "Change it under 'Model' in the sidebar.")
    if isinstance(err, openai.RateLimitError):
        if "insufficient_quota" in str(err):
            return (f"Your {name} account has no remaining credit. "
                    "Add billing or credits, then try again.")
        return (f"You've hit {name}'s rate limit (common on free tiers). "
                "Wait a minute and try again.")
    if isinstance(err, ValueError):
        return f"{name} returned a response that wasn't valid JSON. Please try again."
    return _UNAVAILABLE


def _parse_json(text):
    """Parse a JSON object, tolerating ```json fences or text around it."""
    text = (text or "").strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    fenced = re.search(r"```(?:json)?\s*(.*?)```", text, flags=re.DOTALL)
    if fenced:
        text = fenced.group(1).strip()
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        raise ValueError("No JSON object in model response")
    try:
        return json.loads(text[start:end + 1])
    except json.JSONDecodeError as e:
        raise ValueError("Invalid JSON in model response") from e


# ----------------------------------------------------------------------------
# LOCAL FALLBACK EMBEDDING
# Hashed word + character-trigram counts. Captures spelling-level similarity
# ("PostgreSQL" ~ "Postgres"), not meaning ("ML" vs "Machine Learning"), but
# needs no API, so it works for providers without an embeddings endpoint.
# ----------------------------------------------------------------------------
_LOCAL_DIM = 1024
LOCAL_EMBED_MODEL = "local-text-hash"


def local_embed(text):
    vec = [0.0] * _LOCAL_DIM
    text = (text or "").lower()
    for word in re.findall(r"[a-z0-9+#.]+", text):
        vec[zlib.crc32(("w:" + word).encode()) % _LOCAL_DIM] += 2.0
        padded = f" {word} "
        for i in range(len(padded) - 2):
            vec[zlib.crc32(("c:" + padded[i:i + 3]).encode()) % _LOCAL_DIM] += 1.0
    return vec


# ----------------------------------------------------------------------------
# PROVIDER (any OpenAI-compatible API)
# ----------------------------------------------------------------------------
class OpenAICompatibleProvider(LLMProvider):
    def __init__(self, api_key, config, chat_model=None):
        from openai import OpenAI
        kwargs = {"api_key": api_key}
        if config.get("base_url"):
            kwargs["base_url"] = config["base_url"]
        self.client = OpenAI(**kwargs)
        self.name = config["name"]
        self.chat_model = chat_model or config["chat_model"]
        self.embed_model = config.get("embed_model")
        self.token_multiplier = config.get("token_multiplier", 1)
        self.json_mode = True   # switched off if the provider rejects response_format
        self._key = api_key

    def chat_json(self, system, user, temperature=0.1, max_tokens=1500, label=None):
        last_err = None
        budget = max_tokens * self.token_multiplier
        for attempt in range(2):  # initial try + one automatic retry
            try:
                t0 = time.perf_counter()
                kwargs = {}
                if self.json_mode:
                    kwargs["response_format"] = {"type": "json_object"}
                resp = self.client.chat.completions.create(
                    model=self.chat_model,
                    messages=[
                        {"role": "system", "content": system},
                        {"role": "user", "content": user},
                    ],
                    temperature=temperature,
                    max_tokens=budget,
                    **kwargs,
                )
                u = getattr(resp, "usage", None)
                record_metric(
                    label or self.chat_model, "chat", self.chat_model,
                    time.perf_counter() - t0,
                    getattr(u, "prompt_tokens", 0) or 0,
                    getattr(u, "completion_tokens", 0) or 0,
                )
                return _parse_json(resp.choices[0].message.content)
            except Exception as e:  # noqa: BLE001 - we deliberately retry on any error
                last_err = e
                if _is_key_problem(e):
                    break
                import openai
                if (isinstance(e, openai.BadRequestError) and self.json_mode
                        and "response_format" in str(e)):
                    self.json_mode = False   # retry immediately without JSON mode
                    continue
                if attempt == 0:
                    time.sleep(0.8)
        # Log internally, surface a friendly message (no internal details).
        print(f"[LLM ERROR] {self.name} chat_json failed after retry: {type(last_err).__name__}")
        raise RuntimeError(_friendly_error(last_err, self.name, self.chat_model))

    def embed(self, texts, batch_size=100, label="embeddings"):
        """Embed `texts`. Callers must embed everything they compare in ONE call,
        since a provider can switch to the local fallback mid-session."""
        if not texts:
            return []
        if not self.embed_model:
            return [local_embed(t) for t in texts]
        out = []
        for i in range(0, len(texts), batch_size):
            batch = texts[i:i + batch_size]
            last_err = None
            done = False
            for attempt in range(2):
                try:
                    t0 = time.perf_counter()
                    resp = self.client.embeddings.create(input=batch, model=self.embed_model)
                    out.extend([d.embedding for d in resp.data])
                    u = getattr(resp, "usage", None)
                    record_metric(label, "embed", self.embed_model,
                                  time.perf_counter() - t0,
                                  getattr(u, "prompt_tokens", 0) or 0, 0)
                    done = True
                    break
                except Exception as e:  # noqa: BLE001
                    last_err = e
                    if _is_key_problem(e):
                        break
                    if attempt == 0:
                        time.sleep(0.8)
            if not done:
                print(f"[LLM ERROR] {self.name} embed failed after retry: "
                      f"{type(last_err).__name__}")
                if _is_key_problem(last_err):
                    raise RuntimeError(_friendly_error(last_err, self.name, self.embed_model))
                # Keep the app usable (e.g. free-tier embedding limits): switch this
                # session to local embeddings and redo the whole request locally.
                self.embed_model = None
                return [local_embed(t) for t in texts]
        return out


# Backwards-compatible name.
OpenAIProvider = OpenAICompatibleProvider


# ----------------------------------------------------------------------------
# One provider per user session, rebuilt when that user changes provider, key
# or model. Never shared across sessions, so one user's key is never used for
# another.
# ----------------------------------------------------------------------------
_PROVIDER_KEY = "llm_provider"


def selected_provider_id():
    pid = st.session_state.get(PROVIDER_CHOICE_KEY, DEFAULT_PROVIDER)
    return pid if pid in PROVIDERS else DEFAULT_PROVIDER


def selected_model(provider_id):
    model = (st.session_state.get(model_state_key(provider_id)) or "").strip()
    return model or PROVIDERS[provider_id]["chat_model"]


def get_provider(api_key):
    pid = selected_provider_id()
    model = selected_model(pid)
    sig = (pid, api_key, model)
    cached = st.session_state.get(_PROVIDER_KEY)
    if cached is None or cached[0] != sig:
        cached = (sig, OpenAICompatibleProvider(api_key, PROVIDERS[pid], model))
        st.session_state[_PROVIDER_KEY] = cached
    return cached[1]
