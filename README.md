# RelayAI - Resilient LLM Chatbot with Key Failover & Response Caching

RelayAI is a production-grade conversational AI service built with a React frontend, Python FastAPI backend, LiteLLM gateway, and persistent SQLite storage. It features seamless multi-turn conversation memory, automatic API key failover, deterministic response caching, and live metadata tracking.

---

## 1. Live URLs
- **Frontend (Vercel)**: [https://relay-ai-iota.vercel.app](https://relay-ai-iota.vercel.app)
- **Backend (Railway)**: [https://relay-ai-production-fd31a29a.up.railway.app](https://relay-ai-production-fd31a29a.up.railway.app)
- **Health Check**: [https://relay-ai-production-fd31a29a.up.railway.app/health](https://relay-ai-production-fd31a29a.up.railway.app/health)
- **GitHub Repository**: [https://github.com/aastha-19/relay-ai](https://github.com/aastha-19/relay-ai)

---

## 2. Run locally: with Docker and without

### Prerequisites
- Docker & Docker Compose (or `make`)
- Python 3.10+ (for local backend development)
- Node.js 18+ (for local frontend development)

### Environment Configuration
Copy the `.env.example` template:
```bash
cp .env.example .env
```
Populate `.env` with at least 2 comma-separated API keys:
```env
LLM_API_KEYS=your_first_gemini_api_key,your_second_gemini_api_key
LLM_MODEL=gemini/gemini-flash-lite-latest
DATABASE_URL=sqlite:////data/app.db
ALLOWED_ORIGINS=http://localhost:3000,http://127.0.0.1:3000
ENABLE_FAILURE_SIMULATION=true
```

### Option A: Run with Docker Compose (Recommended)
Build and launch the complete multi-stage containerized stack with a single command:
```bash
make up
```
*(Or `docker compose up --build -d`)*

- **Frontend**: [http://localhost:3000](http://localhost:3000)
- **Backend**: [http://localhost:8000](http://localhost:8000)
- **Health Endpoint**: [http://localhost:8000/health](http://localhost:8000/health)

To stop and remove containers while preserving persistent SQLite data:
```bash
make down
```

### Option B: Run without Docker

#### 1. Backend Service
```bash
cd backend
python -m venv venv
# On Windows:
.\venv\Scripts\activate
# On Linux / macOS:
source venv/bin/activate

pip install -r requirements.txt
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

#### 2. Frontend Application
In a separate terminal:
```bash
cd frontend
npm install
npm run dev
```
Open [http://localhost:3000](http://localhost:3000) in your browser.

#### 3. Run Automated Tests
From the project root:
```bash
pytest backend/tests -v
```

---

## 3. Design decisions: key fallback, caching, SQLite session storage

### Key Fallback Architecture
- **Key Pool**: LiteLLM is configured with an ordered pool of multiple API keys (`key_1`, `key_2`, ...).
- **Automatic Failover**: When any provider returns HTTP 429 (rate limiting), quota exhaustion, or 5xx server errors, the gateway captures the failure, increments `fallback_attempts`, and automatically routes the call to the subsequent key in the pool.
- **Transient Provider Retry**: If the fallback key encounters a transient provider overload spike (HTTP 503 "High Demand"), the gateway executes a brief exponential backoff retry before declaring the key exhausted.
- **Clean Failure & Safety**: If every key in the pool is exhausted, the backend returns a strict HTTP 503 `{"detail": "all providers unavailable"}` without crashing or writing invalid records to SQLite.
- **Failure Simulation (`X-Simulate-Failure`)**: When `ENABLE_FAILURE_SIMULATION=true`, the gateway honors the `X-Simulate-Failure` header (e.g., `key_1=429`, `key_1=500`, or `*=429`) to simulate key-specific or universal provider failures deterministically.

### Response Caching
- **Deterministic Cache Key**: Computed using a SHA-256 hash of the model identifier and the complete ordered sequence of messages (system prompt + conversation history + user prompt).
- **Exact Metadata Contract**:
  - Cache hits return `resolution_source: "LLM_CACHE"`.
  - `key_id` is set to `null` (since no live LLM provider was contacted).
  - `fallback_attempts` is `0`.
  - Token counts (`input_tokens` and `output_tokens`) accurately mirror the tokens cached from the original completion.
  - Cached response latency is dramatically lower (~1–5ms) than live network inference.
- **Cross-Session Caching**: Identical prompts across separate sessions reuse cached responses, significantly reducing upstream latency and API quota consumption.

### SQLite Session Storage
- **Persistent Multi-Turn Memory**: Uses lightweight SQLite for persistent message history across user sessions; no external databases or Redis instances are required.
- **Volume Persistence**: In Docker and on Railway, the database resides at `/data/app.db` on a persistent volume mount (`/data`), ensuring conversation history completely survives service restarts, redeployments, and container recreations.
- **Transaction Isolation**: Failed requests (HTTP 422 validation errors or HTTP 503 provider outages) are never committed to the SQLite database.

---

## 4. Verification: how to see failover, cache hits and per-message token usage

Every assistant response in the deployed web UI displays a comprehensive metadata inspector showing:
- **Source** (`LIVE_INFERENCE` vs `LLM_CACHE`)
- **Model** (`gemini/gemini-flash-lite-latest`)
- **Latency** (in milliseconds)
- **Input Tokens** & **Output Tokens** (per turn, not cumulative)
- **Key Used** (`key_1`, `key_2`, or `null`)
- **Fallback Attempts** (count of failover hops)

### Verification Scenarios

#### 1. Live Inference vs. Cache Hit
1. **Live Request**: In a new session, send `"What is the capital of France?"`.
   - **Result**: `resolution_source: LIVE_INFERENCE`, `key_id: "key_1"`, `fallback_attempts: 0`, and latency ~600–1200ms.
2. **Cache Hit**: Click **New Session** (or refresh page) to obtain a new `session_id`, and send `"What is the capital of France?"` again.
   - **Result**: `resolution_source: LLM_CACHE`, `key_id: null`, `fallback_attempts: 0`, latency ~1–5ms, identical reply text.
3. **Uncached Request**: Send a distinct prompt (e.g., `"What is the capital of Japan?"`).
   - **Result**: Returns to `resolution_source: LIVE_INFERENCE`.

#### 2. Key Failover Simulation (`key_1=429`)
Select `key_1=429` from the UI header dropdown (or send via `curl`):
```bash
curl -X POST https://relay-ai-production-fd31a29a.up.railway.app/chat \
  -H "Content-Type: application/json" \
  -H "X-Simulate-Failure: key_1=429" \
  -d '{"session_id": "test-sim-failover", "message": "Explain key failover in one sentence."}'
```
- **Result**: HTTP 200 OK.
- `key_id: "key_2"`.
- `fallback_attempts: 1`.
- A real Gemini response is generated by the second key.

#### 3. Universal Provider Failure (`*=429`)
Select `*=429` from the UI header dropdown (or send via `curl`):
```bash
curl -X POST https://relay-ai-production-fd31a29a.up.railway.app/chat \
  -H "Content-Type: application/json" \
  -H "X-Simulate-Failure: *=429" \
  -d '{"session_id": "test-sim-all", "message": "This request must fail."}'
```
- **Result**: HTTP 503 Service Unavailable with body `{"detail": "all providers unavailable"}`.
- In the frontend UI, this displays as a readable error message (`Error [503]: all providers unavailable`).
- Verify the failed request was **not** saved to session history:
  ```bash
  curl https://relay-ai-production-fd31a29a.up.railway.app/sessions/test-sim-all/messages
  ```
  Returns `404 {"detail": "session not found"}`.

#### 4. Railway Persistent Volume Verification
The SQLite database at `/data/app.db` is backed by a Railway persistent volume attached to `/data`:
1. Send a chat message under a known session ID (e.g., `test-persist-session`).
2. Verify message history: `GET /sessions/test-persist-session/messages`.
3. Restart or redeploy the Railway backend service via the Railway dashboard.
4. Call `GET /sessions/test-persist-session/messages` again after reboot: the complete conversation and metadata remain intact.

---

## 5. Setup Used: your editor and AI tools
- **Code Editor / IDE**: Visual Studio Code
- **AI Coding Assistant**: Google Antigravity Agent
- **Foundational LLMs**: Gemini 2.5 Pro & Gemini 3.8 Flash
- **Operating System**: Windows 11
- **Runtimes & Frameworks**: Python 3.14 / 3.12, FastAPI, Uvicorn, LiteLLM, React 18, Vite 5, Docker Engine, Nginx Alpine

---

## 6. Bonus features (if any): what you added beyond the requirements and why
1. **Interactive Failure Simulation Dropdown**:
   Built an in-browser failure simulation selector into the header control bar so evaluators can test `key_1=429`, `key_1=500`, and `*=429` without needing terminal commands.
2. **Live Backend Connectivity & Key Health Monitor**:
   The frontend header continuously inspects `GET /health` and displays an active connectivity indicator showing backend health, the number of loaded provider keys, and caching status.
3. **One-Click Session Reset ("New Session")**:
   Allows generating a clean new `session_id` and resetting conversation view instantly without requiring a full browser page refresh.
4. **Transient Provider Demand-Spike Recovery**:
   Added a non-intrusive 1-second backoff retry on the final fallback key to gracefully withstand Google Gemini free-tier 503 "High Demand" spikes.
5. **Cold-Start & Privacy Optimization**:
   Enabled `LITELLM_LOCAL_MODEL_COST_MAP=True` and disabled remote telemetry to deliver ultra-fast container boot times (<200ms) with zero external dependency leaks.
