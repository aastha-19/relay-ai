import os
from typing import List, Tuple
from dotenv import load_dotenv

# Optimize LiteLLM startup to use local cost map and disable telemetry
os.environ["LITELLM_LOCAL_MODEL_COST_MAP"] = "True"
os.environ["LITELLM_TELEMETRY"] = "False"

from pathlib import Path

# Load from .env in current working dir or project root
root_env_path = Path(__file__).resolve().parent.parent.parent / ".env"
if root_env_path.exists():
    load_dotenv(dotenv_path=root_env_path, override=True)
else:
    load_dotenv(override=True)

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./data/app.db")
LLM_MODEL = os.getenv("LLM_MODEL", "gemini/gemini-flash-lite-latest")
# Automatically alias retired/deprecated gemini-2.0-flash to the active gemini-flash-lite-latest
if LLM_MODEL in ("gemini/gemini-2.0-flash", "models/gemini-2.0-flash"):
    LLM_MODEL = "gemini/gemini-flash-lite-latest"
ALLOWED_ORIGINS_RAW = os.getenv("ALLOWED_ORIGINS", "http://localhost:3000,http://127.0.0.1:3000")
ALLOWED_ORIGINS: List[str] = [origin.strip() for origin in ALLOWED_ORIGINS_RAW.split(",") if origin.strip()]

ENABLE_FAILURE_SIMULATION_RAW = os.getenv("ENABLE_FAILURE_SIMULATION", "true")
ENABLE_FAILURE_SIMULATION = ENABLE_FAILURE_SIMULATION_RAW.lower() in ("true", "1", "yes")


def get_key_pool() -> List[Tuple[str, str]]:
    """
    Parses LLM_API_KEYS into an ordered list of (key_id, api_key).
    key_ids are key_1, key_2, ... in order of appearance.
    Reads dynamically from os.environ.
    """
    keys_raw = os.getenv("LLM_API_KEYS", "")
    keys = [k.strip() for k in keys_raw.split(",") if k.strip()]
    if not keys:
        keys = ["dummy_key_1", "dummy_key_2"]
    return [(f"key_{i+1}", key) for i, key in enumerate(keys)]
