import os
os.environ["LITELLM_LOCAL_MODEL_COST_MAP"] = "True"
os.environ["LITELLM_TELEMETRY"] = "False"

import json
import hashlib
import time
import logging
from typing import List, Dict, Any, Optional, Tuple
import litellm

litellm.telemetry = False
litellm.suppress_debug_info = True
litellm.local_model_cost_map = True
from fastapi import HTTPException
from app.config import (
    LLM_MODEL,
    ENABLE_FAILURE_SIMULATION,
    get_key_pool
)

logger = logging.getLogger("llm_gateway")


class SimpleCache:
    """In-memory cache conforming to the assignment cache specification."""
    def __init__(self):
        self._store: Dict[str, Dict[str, Any]] = {}

    def get_cache_key(self, model: str, messages: List[Dict[str, str]]) -> str:
        payload = {"model": model, "messages": messages}
        serialized = json.dumps(payload, sort_keys=True)
        return hashlib.sha256(serialized.encode("utf-8")).hexdigest()

    def get(self, key: str) -> Optional[Dict[str, Any]]:
        return self._store.get(key)

    def set(self, key: str, value: Dict[str, Any]) -> None:
        self._store[key] = value

    def is_enabled(self) -> bool:
        return True


# Global cache instance
llm_cache = SimpleCache()


def parse_simulate_failure_header(header: Optional[str]) -> Dict[str, int]:
    """
    Parses X-Simulate-Failure header.
    Format: comma-separated <key_id>=<status> pairs; * means every key.
    Allowed statuses: 429, 500, 502, 503.
    """
    if not header:
        return {}
    failures = {}
    for part in header.split(","):
        part = part.strip()
        if "=" in part:
            k, status_str = part.split("=", 1)
            k = k.strip()
            try:
                status = int(status_str.strip())
                if status in (429, 500, 502, 503):
                    failures[k] = status
            except ValueError:
                pass
    return failures


def count_tokens(model: str, messages: List[Dict[str, str]], text: str = "") -> int:
    """Calculates prompt or completion token count using LiteLLM or fallback estimation."""
    try:
        if messages:
            return litellm.token_counter(model=model, messages=messages)
        if text:
            return litellm.token_counter(model=model, text=text)
    except Exception:
        pass
    # Fallback heuristic: ~4 chars per token if tokenizer unavailable
    total_chars = sum(len(m.get("content", "")) for m in messages) if messages else len(text)
    return max(1, total_chars // 4)


def execute_llm_call(
    messages_payload: List[Dict[str, str]],
    simulate_failure_header: Optional[str] = None
) -> Dict[str, Any]:
    """
    Executes an LLM request across the key pool with automatic fallback and caching.
    Honors X-Simulate-Failure header when ENABLE_FAILURE_SIMULATION is True.
    """
    start_time = time.perf_counter()
    cache_key = llm_cache.get_cache_key(LLM_MODEL, messages_payload)

    # 1. Check if failure simulation applies
    simulated_failures: Dict[str, int] = {}
    if ENABLE_FAILURE_SIMULATION and simulate_failure_header:
        simulated_failures = parse_simulate_failure_header(simulate_failure_header)

    # 2. Check cache (only if not simulating failures for this request)
    if not simulated_failures:
        cached_entry = llm_cache.get(cache_key)
        if cached_entry:
            latency_ms = max(1, int((time.perf_counter() - start_time) * 1000))
            return {
                "reply": cached_entry["reply"],
                "metadata": {
                    "resolution_source": "LLM_CACHE",
                    "model": cached_entry["model"],
                    "latency_ms": latency_ms,
                    "input_tokens": cached_entry["input_tokens"],
                    "output_tokens": cached_entry["output_tokens"],
                    "key_id": None,
                    "fallback_attempts": 0
                }
            }

    # 3. Key pool failover loop
    key_pool = get_key_pool()
    fallback_attempts = 0

    for key_id, api_key in key_pool:
        # Check simulated failure for this key
        should_fail = False
        fail_status = 500
        if "*" in simulated_failures:
            should_fail = True
            fail_status = simulated_failures["*"]
        elif key_id in simulated_failures:
            should_fail = True
            fail_status = simulated_failures[key_id]

        if should_fail:
            logger.warning(f"Simulating failure for {key_id} with status {fail_status}")
            fallback_attempts += 1
            continue

        try:
            # Call LiteLLM completion
            response = litellm.completion(
                model=LLM_MODEL,
                messages=messages_payload,
                api_key=api_key,
                timeout=30
            )

            reply_text = ""
            if response and response.choices and len(response.choices) > 0:
                reply_text = response.choices[0].message.content or ""

            # Token usage extraction
            usage = getattr(response, "usage", None)
            input_tokens = getattr(usage, "prompt_tokens", None) if usage else None
            output_tokens = getattr(usage, "completion_tokens", None) if usage else None

            if input_tokens is None or input_tokens <= 0:
                input_tokens = count_tokens(LLM_MODEL, messages=messages_payload)
            if output_tokens is None or output_tokens <= 0:
                output_tokens = count_tokens(LLM_MODEL, text=reply_text)

            latency_ms = max(1, int((time.perf_counter() - start_time) * 1000))

            # Populate cache if this was not a simulated run
            if not simulated_failures:
                llm_cache.set(cache_key, {
                    "reply": reply_text,
                    "model": LLM_MODEL,
                    "input_tokens": input_tokens,
                    "output_tokens": output_tokens
                })

            return {
                "reply": reply_text,
                "metadata": {
                    "resolution_source": "LIVE_INFERENCE",
                    "model": LLM_MODEL,
                    "latency_ms": latency_ms,
                    "input_tokens": input_tokens,
                    "output_tokens": output_tokens,
                    "key_id": key_id,
                    "fallback_attempts": fallback_attempts
                }
            }

        except Exception as e:
            logger.warning(f"Provider call failed for {key_id}: {str(e)}")
            fallback_attempts += 1
            continue

    # Every key failed
    logger.error("All providers unavailable in key pool")
    raise HTTPException(status_code=503, detail="all providers unavailable")
