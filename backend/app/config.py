import os
from typing import List, Tuple
from dotenv import load_dotenv

# Optimize LiteLLM startup to use local cost map and disable telemetry
os.environ["LITELLM_LOCAL_MODEL_COST_MAP"] = "True"
os.environ["LITELLM_TELEMETRY"] = "False"

from pathlib import Path

# Load from .env in current working dir or project root only for unset variables
root_env_path = Path(__file__).resolve().parent.parent.parent / ".env"
if root_env_path.exists():
    load_dotenv(dotenv_path=root_env_path, override=False)
else:
    load_dotenv(override=False)

def normalize_model(model_str: str) -> str:
    if not model_str:
        return "gemini/gemini-flash-lite-latest"
    cleaned = model_str.strip().strip('"\'').strip()
    lowered = cleaned.lower()
    # Automatically alias retired/unsupported flash models that return 404 to active gemini-flash-lite-latest
    if any(m in lowered for m in ("2.0-flash", "1.5-flash", "flash-exp")):
        return "gemini/gemini-flash-lite-latest"
    if "gemini" in lowered and not cleaned.startswith("gemini/"):
        return f"gemini/{cleaned}"
    return cleaned


DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./data/app.db")
LLM_MODEL = normalize_model(os.getenv("LLM_MODEL", "gemini/gemini-flash-lite-latest"))
ALLOWED_ORIGINS_RAW = os.getenv("ALLOWED_ORIGINS", "http://localhost:3000,http://127.0.0.1:3000")
ALLOWED_ORIGINS: List[str] = [origin.strip().strip('"\'') for origin in ALLOWED_ORIGINS_RAW.split(",") if origin.strip()]

ENABLE_FAILURE_SIMULATION_RAW = os.getenv("ENABLE_FAILURE_SIMULATION", "true")
ENABLE_FAILURE_SIMULATION = ENABLE_FAILURE_SIMULATION_RAW.lower().strip('"\'') in ("true", "1", "yes")


def get_key_pool() -> List[Tuple[str, str]]:
    """
    Parses LLM_API_KEYS into an ordered list of (key_id, api_key).
    key_ids are key_1, key_2, ... in order of appearance.
    Reads dynamically from os.environ.
    Handles commas, newlines, semicolons, quotes, and optional alternate env vars.
    """
    keys_raw = os.getenv("LLM_API_KEYS", "")
    keys_raw = keys_raw.strip().strip('"\'')
    normalized = keys_raw.replace("\r", "\n").replace(";", ",")
    raw_list: List[str] = []
    for line in normalized.split("\n"):
        for part in line.split(","):
            cleaned = part.strip().strip('"\'').strip()
            if cleaned and cleaned not in raw_list:
                raw_list.append(cleaned)

    # Check alternative env vars if less than 2 keys configured
    if len(raw_list) < 2:
        for alt_var in ("LLM_API_KEY_1", "LLM_API_KEY_2", "GEMINI_API_KEY_1", "GEMINI_API_KEY_2", "GEMINI_API_KEY"):
            val = os.getenv(alt_var, "").strip().strip('"\'').strip()
            if val and val not in raw_list:
                raw_list.append(val)

    if not raw_list:
        raw_list = ["dummy_key_1", "dummy_key_2"]
    return [(f"key_{i+1}", key) for i, key in enumerate(raw_list)]
