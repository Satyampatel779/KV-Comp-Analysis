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
    )
