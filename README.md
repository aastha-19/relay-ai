# Lyftr AI Assignment: Resilient LLM Chatbot with Key Failover and Response Caching

## 1. Live URLs
- **Frontend Live URL**: https://lyftr-frontend.example.com *(Replace with your deployed frontend URL e.g. on Vercel / Netlify / Render)*
- **Backend Live URL**: https://lyftr-backend.example.com *(Replace with your deployed backend URL e.g. on Render / Railway / Fly.io)*

## 2. Run locally: with Docker and without

### Prerequisites
- Docker & Docker Compose (or `make`)
- Python 3.10+ (for running backend locally without Docker)
- Node.js 18+ (for running frontend locally without Docker)

### Environment Configuration
Copy the `.env.example` file to create your local `.env`:
```bash
cp .env.example .env
```
Ensure at least 2 comma-separated API keys are specified in `LLM_API_KEYS`:
```env
LLM_API_KEYS=your_first_api_key,your_second_api_key
LLM_MODEL=gemini/gemini-2.0-flash
DATABASE_URL=sqlite:////data/app.db
ALLOWED_ORIGINS=http://localhost:3000,http://127.0.0.1:3000
ENABLE_FAILURE_SIMULATION=true
```

### Run with Docker Compose (Recommended)
Using the Makefile:
```bash
make up
```
Or directly with Docker Compose:
```bash
docker compose up --build -d
```
- **Frontend**: [http://localhost:3000](http://localhost:3000)
- **Backend**: [http://localhost:8000](http://localhost:8000)
- **Health Check**: [http://localhost:8000/health](http://localhost:8000/health)

To stop and remove containers:
```bash
make down
# or docker compose down
```

### Run without Docker

#### Backend
1. Open a terminal and navigate to the `backend/` folder:
   ```bash
   cd backend
   python -m venv venv
   # On Windows:
   .\venv\Scripts\activate
   # On Linux / macOS:
   source venv/bin/activate
   pip install -r requirements.txt
   ```
2. Start the FastAPI server:
   ```bash
   uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
   ```

#### Frontend
1. Open a separate terminal and navigate to the `frontend/` folder:
   ```bash
   cd frontend
   npm install
   npm run dev
   ```
2. Open [http://localhost:3000](http://localhost:3000) in your browser.

#### Running Backend Automated Tests
From repository root:
```bash
python -m pytest backend/tests -v
```

## 3. Design decisions: key fallback, caching, SQLite session storage

### Key Fallback Architecture
- **Key Pool**: LiteLLM is configured with an ordered pool of at least 2 API keys (`key_1`, `key_2`, ...).
- **Automatic Failover**: When a provider encounters HTTP 429 (rate limiting), quota exhaustion, or 5xx server errors, the gateway captures the failure, increments `fallback_attempts`, and automatically retries with the subsequent key in the pool.
- **Graceful Failure**: If all keys in the pool fail, the endpoint returns a clean HTTP 503 response with `{"detail": "all providers unavailable"}` without dumping stack traces or corrupting session history.
- **Failure Simulation (`X-Simulate-Failure`)**: When `ENABLE_FAILURE_SIMULATION=true`, the gateway inspects the `X-Simulate-Failure` header (e.g. `key_1=429`, `key_1=500`, or `*=429`) and simulates provider errors for specific keys per-request. A successful fallback returns real model output.

### Response Caching
- **Deterministic Cache Key**: The cache key is computed as a SHA-256 hash of the model name combined with the complete ordered list of messages (system prompt + past history + new user message).
- **Exact Cache Contract**:
  - `resolution_source` is set to `LLM_CACHE`.
  - `key_id` is set to `null` (since no API key was invoked).
  - `fallback_attempts` is set to `0`.
  - Input and output tokens reflect the tokens stored with the cached response.
  - Latency is significantly faster than live inference.
- **Cross-Session Caching**: Two separate sessions sending the same first message (with default system prompt) will result in `LIVE_INFERENCE` on the first call and `LLM_CACHE` on the second call.

### SQLite Session Storage
- **Local Persistence**: Strictly adheres to SQLite; no Redis or Postgres services are required.
- **Docker Volume**: The database path defaults to `/data/app.db` mounted to the `lyftr_sqlite_data` named Docker volume, ensuring session history survives container restarts and upgrades.
- **Schema Auto-Migration**: Database tables (`messages` and `sessions`) and indexes are auto-created on application startup.
- **Transaction Safety**: Failed requests (422 validation errors or 503 provider outages) are never committed to the database.

## 4. Verification: how to see failover, cache hits and per-message token usage

### 1. Verify Cache Hits (`LIVE_INFERENCE` vs `LLM_CACHE`)
1. On the web interface, enter `"What is the capital of France?"` and press **Send**.
   - Notice the badge: `LIVE_INFERENCE`, `key_id: "key_1"`, with measured latency (e.g., 600–900ms).
2. Click **New Session** (or refresh the page) to get a new `session_id`, then send `"What is the capital of France?"` again.
   - Notice the badge: `LLM_CACHE`, `key_id: null`, `fallback_attempts: 0`, and latency (~1–3ms).
   - The response text is identical.
3. Send a new message: `"What is the capital of Japan?"`.
   - Notice the badge returns to `LIVE_INFERENCE`.

### 2. Verify Key Failover with `X-Simulate-Failure`
Use the failure simulation dropdown on the UI control bar, or test with `curl`:
- **Simulate key_1=429 (Fallback to key_2)**:
  ```bash
  curl -X POST http://localhost:8000/chat \
    -H "Content-Type: application/json" \
    -H "X-Simulate-Failure: key_1=429" \
    -d '{"session_id": "test-sim-1", "message": "Verify failover"}'
  ```
  Response contains: `"key_id": "key_2"`, `"fallback_attempts": 1`, and real answer.

- **Simulate *=429 (All keys fail -> 503)**:
  ```bash
  curl -X POST http://localhost:8000/chat \
    -H "Content-Type: application/json" \
    -H "X-Simulate-Failure: *=429" \
    -d '{"session_id": "test-sim-all", "message": "Verify 503"}'
  ```
  Response status: `503 Service Unavailable`, body `{"detail": "all providers unavailable"}`.
  Verify history is empty:
  ```bash
  curl http://localhost:8000/sessions/test-sim-all/messages
  ```
  Returns `404 {"detail": "session not found"}`.

### 3. Verify Token Usage per Request
- Look at the `In Tokens` (`meta-input-tokens`) and `Out Tokens` (`meta-output-tokens`) badges under each assistant message.
- These reflect tokens consumed by that specific prompt turn and completion, never a running cumulative total.

## 5. Setup Used: your editor and AI tools
- **Operating System**: Windows 11
- **Code Editor / IDE**: Visual Studio Code & Google Antigravity IDE
- **AI Tools**: Google Antigravity Agent, Gemini 3.8 Flash
- **Language / Framework Versions**: Python 3.14 / 3.12, FastAPI 0.115+, Uvicorn 0.34+, LiteLLM 1.110+, Node.js 20, React 18, Vite 5.

## 6. Bonus features (if any): what you added beyond the requirements and why
1. **Interactive Failure Simulation Dropdown**:
   Added a convenient UI dropdown allowing users or evaluators to test `key_1=429`, `key_1=500`, and `*=429` directly from the browser without opening a terminal or Postman.
2. **Real-time Backend Health & Key Status Badge**:
   The chat header actively polls `/health` and displays the server connectivity, the number of configured keys in the pool, and the caching status.
3. **One-Click Session Reset ("New Session")**:
   Allows generating a new `session_id` and clearing the chat window without manually refreshing the browser.
4. **LiteLLM Cold-Start Optimization**:
   Preconfigured `LITELLM_LOCAL_MODEL_COST_MAP=True` and disabled remote telemetry to ensure fast container boot times (<200ms) even in air-gapped or restricted network environments.
