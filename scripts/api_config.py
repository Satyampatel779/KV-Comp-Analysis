"""Environment configuration for the KV comp-analysis API.

Reads settings from environment variables (optionally from a local ``.env``
file if ``python-dotenv`` is installed). The same ``MONGODB_URI`` already used
by the CLI ranking script is reused here.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache

try:  # optional convenience for local development
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:  # python-dotenv not installed — plain env vars still work
    pass


@dataclass(frozen=True)
class Settings:
    mongodb_uri: str
    mongodb_db: str
    api_host: str
    api_port: int
    api_key: str | None
    groq_api_key: str | None
    groq_model: str
    groq_base_url: str
    google_maps_api_key: str | None


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    uri = os.environ.get("MONGODB_URI")
    if not uri:
        raise RuntimeError(
            "MONGODB_URI is not set. Export it (the same URI the CLI uses) "
            "or add it to a .env file before starting the API."
        )

    return Settings(
        mongodb_uri=uri,
        mongodb_db=os.environ.get("MONGODB_DB", "kv_comp_analysis"),
        api_host=os.environ.get("API_HOST", "0.0.0.0"),
        api_port=int(os.environ.get("API_PORT", "8000")),
        api_key=os.environ.get("API_KEY") or None,
        groq_api_key=os.environ.get("GROQ_API_KEY") or None,
        groq_model=os.environ.get("GROQ_MODEL", "llama-3.3-70b-versatile"),
        groq_base_url=os.environ.get("GROQ_BASE_URL", "https://api.groq.com/openai/v1"),
        google_maps_api_key=os.environ.get("GOOGLE_MAPS_API_KEY") or None,
    )
