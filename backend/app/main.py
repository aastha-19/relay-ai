import logging
from contextlib import asynccontextmanager
from typing import Optional
from fastapi import FastAPI, Header, HTTPException, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.exceptions import RequestValidationError

from app.config import (
    ALLOWED_ORIGINS,
    get_key_pool
)
from app.database import (
    init_db,
    save_session_prompt,
    get_session_prompt,
    get_session_messages,
    session_exists,
    save_message_pair,
    get_connection
)
from app.models import (
    ChatRequest,
    ChatResponse,
    ChatMetadata,
    HealthResponse,
    SessionHistoryResponse,
    SessionMessageItem,
    SessionMessageMetadata
)
from app.llm_gateway import (
    execute_llm_call,
    llm_cache
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("app")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Initialize SQLite database on startup
    init_db()
    logger.info("Database initialized successfully.")
    pool = get_key_pool()
    from app.config import LLM_MODEL
    logger.info(f"LLM Gateway ready with model={LLM_MODEL}, {len(pool)} keys loaded: {[k[0] for k in pool]}")
    yield


app = FastAPI(
    title="Lyftr AI Chatbot Service",
    lifespan=lifespan
)

# CORS Middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS if ALLOWED_ORIGINS else ["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


from fastapi.encoders import jsonable_encoder
from fastapi.exception_handlers import request_validation_exception_handler


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        content={"detail": jsonable_encoder(exc.errors())}
    )


@app.exception_handler(Exception)
async def generic_exception_handler(request: Request, exc: Exception):
    if isinstance(exc, HTTPException):
        return JSONResponse(
            status_code=exc.status_code,
            content={"detail": exc.detail}
        )
    logger.exception("Unhandled server error")
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={"detail": "Internal server error"}
    )


@app.get("/health", response_model=HealthResponse)
def health_check():
    """Returns 200 once app is running, DB is reachable, and key pool is loaded."""
    # Check DB reachability
    try:
        conn = get_connection()
        conn.execute("SELECT 1")
        conn.close()
    except Exception as e:
        logger.error(f"Health check failed DB reachability: {e}")
        raise HTTPException(status_code=500, detail="Database unreachable")

    keys = get_key_pool()
    return HealthResponse(
        status="ok",
        keys_configured=len(keys),
        cache_enabled=llm_cache.is_enabled()
    )


@app.post("/chat", response_model=ChatResponse)
def chat_endpoint(
    req: ChatRequest,
    x_simulate_failure: Optional[str] = Header(None, alias="X-Simulate-Failure")
):
    """
    POST /chat
    Executes a multi-turn conversation turn with automatic key failover,
    response caching, and optional failure simulation.
    """
    # 1. Fetch conversation history
    history = get_session_messages(req.session_id)

    # 2. Handle system prompt (override session system prompt if provided)
    if req.system_prompt:
        save_session_prompt(req.session_id, req.system_prompt)
        effective_system_prompt = req.system_prompt
    else:
        effective_system_prompt = get_session_prompt(req.session_id) or "You are a helpful assistant."

    # 3. Construct messages payload
    messages_payload = []
    if effective_system_prompt:
        messages_payload.append({"role": "system", "content": effective_system_prompt})

    for item in history:
        messages_payload.append({"role": item["role"], "content": item["content"]})

    messages_payload.append({"role": "user", "content": req.message})

    # 4. Execute LLM call with failover and caching
    # If 503 is raised, nothing is saved to DB
    result = execute_llm_call(
        messages_payload=messages_payload,
        simulate_failure_header=x_simulate_failure
    )

    reply_text = result["reply"]
    metadata_dict = result["metadata"]

    # 5. Save message pair to SQLite (only on success)
    save_message_pair(
        session_id=req.session_id,
        user_content=req.message,
        assistant_content=reply_text,
        assistant_metadata=metadata_dict
    )

    metadata = ChatMetadata(**metadata_dict)
    return ChatResponse(
        session_id=req.session_id,
        reply=reply_text,
        metadata=metadata
    )


@app.get(
    "/sessions/{session_id}/messages",
    response_model=SessionHistoryResponse,
    response_model_exclude_none=True
)
def get_session_history(session_id: str):
    """
    GET /sessions/{session_id}/messages
    Returns the stored history of one session from SQLite, oldest first, without the system prompt.
    Returns 404 if session is unknown.
    """
    if not session_exists(session_id):
        raise HTTPException(status_code=404, detail="session not found")

    raw_messages = get_session_messages(session_id)
    items = []
    for m in raw_messages:
        meta = None
        if m["role"] == "assistant" and m["metadata"]:
            meta = SessionMessageMetadata(
                resolution_source=m["metadata"].get("resolution_source", "LIVE_INFERENCE"),
                input_tokens=m["metadata"].get("input_tokens", 0),
                output_tokens=m["metadata"].get("output_tokens", 0)
            )
        items.append(SessionMessageItem(
            role=m["role"],
            content=m["content"],
            metadata=meta
        ))

    return SessionHistoryResponse(session_id=session_id, messages=items)
