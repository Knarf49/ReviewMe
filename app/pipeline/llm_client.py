"""
LLM client factory — pick OpenAI or Ollama at call time.

Ollama exposes an OpenAI-compatible REST API at
http://localhost:11434/v1, so the same `openai` SDK works for both
providers — only base_url + api_key change.

Public surface:
    PROVIDERS                       -- ("openai", "ollama")
    DEFAULT_MODELS                  -- per-provider default model name
    get_sync_client(provider)       -- OpenAI()
    get_async_client(provider)      -- AsyncOpenAI()
    resolve_model(provider, model)  -- str (falls back to env / default)
    supports_json_mode(provider, model) -- bool
"""

from __future__ import annotations

import os

from openai import AsyncOpenAI, OpenAI

PROVIDERS = ("openai", "ollama", "openrouter")

DEFAULT_MODELS: dict[str, str] = {
    "openai": "gpt-5.4-mini",
    "ollama": "qwen3:8b",
    "openrouter": "openai/gpt-4o-mini",
}

OLLAMA_BASE_URL = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434/v1")
OLLAMA_API_KEY = "ollama"  # any non-empty string; Ollama ignores it
OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"


def _normalize(provider: str | None, env_key: str = "LLM_PROVIDER") -> str:
    p = (provider or os.environ.get(env_key) or "openai").lower().strip()
    if p not in PROVIDERS:
        raise ValueError(
            f"Unknown LLM provider {p!r}. Expected one of: {PROVIDERS}"
        )
    return p


def resolve_model(provider: str | None = None, model: str | None = None, provider_env_key: str = "LLM_PROVIDER") -> str:
    """Pick a concrete model name: explicit > env > default."""
    p = _normalize(provider, provider_env_key)
    if model and model.strip():
        return model.strip()
    env_key = {"ollama": "OLLAMA_MODEL", "openrouter": "OPENROUTER_MODEL"}.get(p, "OPENAI_MODEL")
    return os.environ.get(env_key) or DEFAULT_MODELS[p]


def _openai_kwargs(provider: str) -> dict:
    if provider == "ollama":
        return {"api_key": OLLAMA_API_KEY, "base_url": OLLAMA_BASE_URL}
    if provider == "openrouter":
        api_key = os.environ.get("OPENROUTER_API_KEY")
        if not api_key:
            raise RuntimeError("OPENROUTER_API_KEY missing.")
        return {"api_key": api_key, "base_url": OPENROUTER_BASE_URL}
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError(
            "OPENAI_API_KEY missing. Add it to .env or pick the ollama provider."
        )
    return {"api_key": api_key}


def get_sync_client(provider: str | None = None, provider_env_key: str = "LLM_PROVIDER") -> OpenAI:
    return OpenAI(**_openai_kwargs(_normalize(provider, provider_env_key)))


def get_async_client(provider: str | None = None, provider_env_key: str = "LLM_PROVIDER") -> AsyncOpenAI:
    return AsyncOpenAI(**_openai_kwargs(_normalize(provider, provider_env_key)))


def supports_json_mode(provider: str | None = None, model: str | None = None) -> bool:
    """Ollama 0.5+ accepts response_format json_object on most chat models;
    treat as supported. OpenAI always supports it on the models we use.
    """
    _normalize(provider)
    return True


def provider_label(provider: str | None = None, model: str | None = None) -> str:
    p = _normalize(provider)
    m = resolve_model(p, model)
    return f"{p}:{m}"
