import os
import json
import time
import pytest
from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient

# Ensure test configuration
os.environ["DATABASE_URL"] = "sqlite:///./test_app.db"
os.environ["LLM_API_KEYS"] = "test_key_1,test_key_2"
os.environ["LLM_MODEL"] = "gemini/gemini-2.0-flash"
os.environ["ENABLE_FAILURE_SIMULATION"] = "true"

from app.main import app
from app.database import init_db, get_connection
from app.llm_gateway import llm_cache


def create_mock_completion_response(content: str = "Test response from LLM", prompt_tokens: int = 42, completion_tokens: int = 9):
    mock_choice = MagicMock()
    mock_choice.message.content = content
    mock_resp = MagicMock()
    mock_resp.choices = [mock_choice]
    mock_resp.usage.prompt_tokens = prompt_tokens
    mock_resp.usage.completion_tokens = completion_tokens
    return mock_resp


@pytest.fixture(autouse=True)
def setup_and_teardown():
    # Clear test database
    if os.path.exists("./test_app.db"):
        try:
            os.remove("./test_app.db")
        except Exception:
            pass
    # Clear cache
    llm_cache._store.clear()
    init_db()
    yield
    if os.path.exists("./test_app.db"):
        try:
            os.remove("./test_app.db")
        except Exception:
            pass


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


def test_health_check(client):
    response = client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert data["keys_configured"] == 2
    assert data["cache_enabled"] is True


def test_validation_errors_422(client):
    # Empty message
    r = client.post("/chat", json={"session_id": "s-1", "message": ""})
    assert r.status_code == 422
    assert "detail" in r.json()

    # Whitespace message
    r = client.post("/chat", json={"session_id": "s-1", "message": "   "})
    assert r.status_code == 422
    assert "detail" in r.json()

    # Missing session_id
    r = client.post("/chat", json={"message": "hello"})
    assert r.status_code == 422

    # Empty session_id
    r = client.post("/chat", json={"session_id": "", "message": "hello"})
    assert r.status_code == 422

    # Over 4000 chars message
    r = client.post("/chat", json={"session_id": "s-1", "message": "a" * 4001})
    assert r.status_code == 422


def test_unknown_session_404(client):
    r = client.get("/sessions/unknown-session-123/messages")
    assert r.status_code == 404
    assert r.json() == {"detail": "session not found"}


@patch("litellm.completion")
def test_normal_chat_and_caching(mock_completion, client):
    mock_completion.return_value = create_mock_completion_response(
        content="The capital of France is Paris.",
        prompt_tokens=42,
        completion_tokens=9
    )

    # First turn: Live inference
    req1 = {"session_id": "session-cache-1", "message": "What is the capital of France?"}
    r1 = client.post("/chat", json=req1)
    assert r1.status_code == 200
    data1 = r1.json()
    assert data1["session_id"] == "session-cache-1"
    assert data1["reply"] == "The capital of France is Paris."
    assert data1["metadata"]["resolution_source"] == "LIVE_INFERENCE"
    assert data1["metadata"]["key_id"] == "key_1"
    assert data1["metadata"]["fallback_attempts"] == 0
    assert data1["metadata"]["input_tokens"] == 42
    assert data1["metadata"]["output_tokens"] == 9
    assert data1["metadata"]["latency_ms"] >= 0

    # Second request in a NEW session with identical message -> Must return LLM_CACHE
    req2 = {"session_id": "session-cache-2", "message": "What is the capital of France?"}
    r2 = client.post("/chat", json=req2)
    assert r2.status_code == 200
    data2 = r2.json()
    assert data2["session_id"] == "session-cache-2"
    assert data2["reply"] == "The capital of France is Paris."  # Identical reply
    assert data2["metadata"]["resolution_source"] == "LLM_CACHE"
    assert data2["metadata"]["key_id"] is None  # null in JSON
    assert data2["metadata"]["fallback_attempts"] == 0
    assert data2["metadata"]["input_tokens"] == 42
    assert data2["metadata"]["output_tokens"] == 9

    # Third request with DIFFERENT message -> Live inference
    req3 = {"session_id": "session-cache-3", "message": "What is the capital of Germany?"}
    mock_completion.return_value = create_mock_completion_response(content="The capital of Germany is Berlin.")
    r3 = client.post("/chat", json=req3)
    assert r3.status_code == 200
    data3 = r3.json()
    assert data3["metadata"]["resolution_source"] == "LIVE_INFERENCE"
    assert data3["metadata"]["key_id"] == "key_1"


@patch("litellm.completion")
def test_multi_turn_conversation_and_history(mock_completion, client):
    mock_completion.side_effect = [
        create_mock_completion_response(content="Hello Alice!", prompt_tokens=12, completion_tokens=5),
        create_mock_completion_response(content="Your name is Alice.", prompt_tokens=25, completion_tokens=7)
    ]

    session_id = "s-alice-123"

    # Turn 1
    r1 = client.post("/chat", json={"session_id": session_id, "message": "Hello, my name is Alice."})
    assert r1.status_code == 200
    assert r1.json()["reply"] == "Hello Alice!"

    # Turn 2
    r2 = client.post("/chat", json={"session_id": session_id, "message": "What is my name?"})
    assert r2.status_code == 200
    assert r2.json()["reply"] == "Your name is Alice."

    # Verify history endpoint
    r_hist = client.get(f"/sessions/{session_id}/messages")
    assert r_hist.status_code == 200
    hist = r_hist.json()
    assert hist["session_id"] == session_id
    msgs = hist["messages"]
    assert len(msgs) == 4

    # Message 0: User (no metadata)
    assert msgs[0]["role"] == "user"
    assert msgs[0]["content"] == "Hello, my name is Alice."
    assert "metadata" not in msgs[0]

    # Message 1: Assistant (has metadata)
    assert msgs[1]["role"] == "assistant"
    assert msgs[1]["content"] == "Hello Alice!"
    assert msgs[1]["metadata"]["resolution_source"] == "LIVE_INFERENCE"
    assert msgs[1]["metadata"]["input_tokens"] == 12
    assert msgs[1]["metadata"]["output_tokens"] == 5

    # Message 2: User
    assert msgs[2]["role"] == "user"
    assert msgs[2]["content"] == "What is my name?"
    assert "metadata" not in msgs[2]

    # Message 3: Assistant
    assert msgs[3]["role"] == "assistant"
    assert msgs[3]["content"] == "Your name is Alice."


@patch("litellm.completion")
def test_sessions_remain_separate(mock_completion, client):
    mock_completion.return_value = create_mock_completion_response(content="Response")

    client.post("/chat", json={"session_id": "session-a", "message": "Message for A"})
    client.post("/chat", json={"session_id": "session-b", "message": "Message for B"})

    hist_a = client.get("/sessions/session-a/messages").json()
    hist_b = client.get("/sessions/session-b/messages").json()

    assert len(hist_a["messages"]) == 2
    assert hist_a["messages"][0]["content"] == "Message for A"

    assert len(hist_b["messages"]) == 2
    assert hist_b["messages"][0]["content"] == "Message for B"


@patch("litellm.completion")
def test_simulate_failure_key_1_429(mock_completion, client):
    mock_completion.return_value = create_mock_completion_response(content="Fallback answer from key_2")

    r = client.post(
        "/chat",
        json={"session_id": "s-sim-1", "message": "Test failover 429"},
        headers={"X-Simulate-Failure": "key_1=429"}
    )
    assert r.status_code == 200
    data = r.json()
    assert data["reply"] == "Fallback answer from key_2"
    assert data["metadata"]["key_id"] == "key_2"
    assert data["metadata"]["fallback_attempts"] == 1


@patch("litellm.completion")
def test_simulate_failure_key_1_500(mock_completion, client):
    mock_completion.return_value = create_mock_completion_response(content="Fallback answer from key_2")

    r = client.post(
        "/chat",
        json={"session_id": "s-sim-2", "message": "Test failover 500"},
        headers={"X-Simulate-Failure": "key_1=500"}
    )
    assert r.status_code == 200
    data = r.json()
    assert data["reply"] == "Fallback answer from key_2"
    assert data["metadata"]["key_id"] == "key_2"
    assert data["metadata"]["fallback_attempts"] == 1


def test_simulate_failure_all_keys_503_and_no_history(client):
    session_id = "s-sim-fail-all"
    r = client.post(
        "/chat",
        json={"session_id": session_id, "message": "All keys must fail"},
        headers={"X-Simulate-Failure": "*=429"}
    )
    assert r.status_code == 503
    assert r.json() == {"detail": "all providers unavailable"}

    # Must NOT be added to history
    r_hist = client.get(f"/sessions/{session_id}/messages")
    assert r_hist.status_code == 404
    assert r_hist.json() == {"detail": "session not found"}


@patch("litellm.completion")
def test_real_provider_error_failover(mock_completion, client):
    # First key throws an exception (e.g. RateLimit 429 or 500), second key succeeds
    mock_completion.side_effect = [
        Exception("Rate limit 429 exceeded"),
        create_mock_completion_response(content="Succeeded via key_2 after provider error", prompt_tokens=20, completion_tokens=10)
    ]

    r = client.post(
        "/chat",
        json={"session_id": "s-provider-fail", "message": "Trigger provider error"}
    )
    assert r.status_code == 200
    data = r.json()
    assert data["reply"] == "Succeeded via key_2 after provider error"
    assert data["metadata"]["key_id"] == "key_2"
    assert data["metadata"]["fallback_attempts"] == 1


def test_frontend_contract_data_testids():
    with open("frontend/src/App.jsx", "r", encoding="utf-8") as f:
        src = f.read()

    required_ids = [
        "chat-input",
        "send-button",
        "assistant-message",
        "meta-source",
        "meta-model",
        "meta-latency",
        "meta-input-tokens",
        "meta-output-tokens",
        "system-prompt-input"
    ]
    for tid in required_ids:
        assert f'data-testid="{tid}"' in src, f"Missing required data-testid: {tid}"
