"""
Modular LLM provider layer.
=================================================================
The rest of the app talks to this layer, never to OpenAI directly. To switch
providers (Anthropic, Gemini, a local model), add a class implementing
`chat_json()` and `embed()` and point `get_provider()` at it — no other file
changes. Prompts live in skills/*.md (centralised); models/params live here.

Guardrails implemented here:
- Deterministic-where-possible: callers pass low temperatures.
- Error handling: every call retries ONCE automatically; on a second failure a
  user-friendly RuntimeError is raised (internal details are not surfaced).
- JSON-only responses for structured agents.
"""

import json
import time

import streamlit as st

# ----------------------------------------------------------------------------
# CALL METRICS (latency + token usage), used by the Evaluation tab.
# Kept in each user's session so users never see each other's usage.
# Approximate USD rates (per 1M tokens) — clearly an estimate, edit as needed.
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


_UNAVAILABLE = ("The AI service is temporarily unavailable. Your data is safe — "
                "please try again.")


def _is_key_problem(err):
    """Errors caused by the user's own key; retrying will not help."""
    import openai
    return isinstance(err, (openai.AuthenticationError, openai.PermissionDeniedError))


def _friendly_error(err):
    """Map an OpenAI error to a message the user can act on (no internal details)."""
    import openai
    if isinstance(err, openai.AuthenticationError):
        return "Your OpenAI API key was rejected. Check the key in the sidebar and try again."
    if isinstance(err, openai.PermissionDeniedError):
        return "Your OpenAI API key does not have access to the required model."
    if isinstance(err, openai.RateLimitError) and "insufficient_quota" in str(err):
        return ("Your OpenAI account has no remaining credit. Add billing at "
                "platform.openai.com, then try again.")
    return _UNAVAILABLE


class OpenAIProvider(LLMProvider):
    def __init__(self, api_key, chat_model="gpt-4o", embed_model="text-embedding-3-small"):
        from openai import OpenAI
        self.client = OpenAI(api_key=api_key)
        self.chat_model = chat_model
        self.embed_model = embed_model
        self._key = api_key

    def chat_json(self, system, user, temperature=0.1, max_tokens=1500, label=None):
        last_err = None
        for attempt in range(2):  # initial try + one automatic retry
            try:
                t0 = time.perf_counter()
                resp = self.client.chat.completions.create(
                    model=self.chat_model,
                    messages=[
                        {"role": "system", "content": system},
                        {"role": "user", "content": user},
                    ],
                    temperature=temperature,
                    max_tokens=max_tokens,
                    response_format={"type": "json_object"},
                )
                u = getattr(resp, "usage", None)
                record_metric(
                    label or self.chat_model, "chat", self.chat_model,
                    time.perf_counter() - t0,
                    getattr(u, "prompt_tokens", 0) or 0,
                    getattr(u, "completion_tokens", 0) or 0,
                )
                return json.loads(resp.choices[0].message.content.strip())
            except Exception as e:  # noqa: BLE001 - we deliberately retry on any error
                last_err = e
                if _is_key_problem(e):
                    break
                if attempt == 0:
                    time.sleep(0.8)
        # Log internally, surface a friendly message (no internal details).
        print(f"[LLM ERROR] chat_json failed after retry: {type(last_err).__name__}")
        raise RuntimeError(_friendly_error(last_err))

    def embed(self, texts, batch_size=100, label="embeddings"):
        if not texts:
            return []
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
                print(f"[LLM ERROR] embed failed after retry: {type(last_err).__name__}")
                raise RuntimeError(_friendly_error(last_err))
        return out


# One provider per user session (rebuilt only if that user's API key changes).
# Never shared across sessions, so one user's key is never used for another.
_PROVIDER_KEY = "llm_provider"


def get_provider(api_key):
    provider = st.session_state.get(_PROVIDER_KEY)
    if provider is None or getattr(provider, "_key", None) != api_key:
        provider = OpenAIProvider(api_key)
        st.session_state[_PROVIDER_KEY] = provider
    return provider
