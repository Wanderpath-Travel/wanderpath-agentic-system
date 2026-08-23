# 🐳 Wanderpath Autonomous Concierge — Production Docker Deployment Guide

> **Enterprise Multi-Agent Travel Orchestration Platform v3.0**  
> Reproducible, multi-service containerized architecture with zero-dependency deterministic runtime and live LLM provider support.

---

## 🏗️ 1. Container Architecture Overview

```
                      [ Host Browser / Client ]
                                  │
         ┌────────────────────────┴────────────────────────┐
         │                                                 │
    (Port :8500)                                      (Port :8000)
         │                                                 │
         ▼                                                 ▼
┌─────────────────────────────────┐               ┌─────────────────────────────────┐
│       wanderpath-platform       │               │         wanderpath-mcp          │
│   (FastAPI / Starlette ASGI)    │               │    (FastMCP Streamable SSE)     │
│                                 │               │                                 │
│  • 5 Autonomous Agent Desks     │ ◄───────────► │  • Dynamic MCP Tool Registry    │
│  • Admin Command Center & UI    │ (Docker Net)  │  • Live Enabled/Disabled State  │
│  • HITL & Failure Ticket Engine │               │  • Flight/Hotel/Medical Tools   │
│  • Real-World Webhook Ingestion │               │                                 │
└────────────────┬────────────────┘               └─────────────────────────────────┘
                 │
                 ├──► [ Volume: wanderpath_db_data ]       (/app/db/wanderpath.sqlite3)
                 └──► [ Volume: wanderpath_chroma_data ]   (/app/rag/chroma_db)
```

### Exposed Service Ports:
| Service | Container Port | Host Port | Protocol | Description |
|---|---|---|---|---|
| **Platform Web UI & API** | `8500` | `8500` | HTTP / ASGI | Full Concierge chat portal, Admin Command Center, and Webhooks |
| **Model Context Protocol (MCP)** | `8000` | `8000` | HTTP / SSE | Streamable FastMCP server with dynamic tool registry |

---

## 🚀 2. Quickstart with Docker Compose

### Prerequisites
- [Docker Engine](https://docs.docker.com/engine/install/) 20.10+
- [Docker Compose](https://docs.docker.com/compose/install/) v2.0+

### Step 1: Clone & Configure Environment
```bash
# Copy example environment file
cp .env.example .env

# (Optional) Add your live API keys to .env if you want real LLM calls
# nano .env
```

### Step 2: Build & Launch Services
```bash
docker compose up --build -d
```

### Step 3: Access the Platform
- **Luxury Concierge Portal & Admin Surface**: [http://localhost:8500](http://localhost:8500)
- **MCP Server SSE Endpoint**: [http://localhost:8000/sse](http://localhost:8000/sse)
- **Container Healthcheck**: [http://localhost:8500/healthz](http://localhost:8500/healthz)

---

## 📦 3. Single-Container All-in-One Mode

If you prefer running a single standalone container running both services simultaneously:

```bash
# 1. Build the unified image
docker build -t wanderpath-autonomous-platform:latest .

# 2. Run with persistent volume mounts and dual-port forwarding
docker run -d \
  --name wanderpath-app \
  -p 8500:8500 \
  -p 8000:8000 \
  -v wanderpath_db:/app/db \
  -v wanderpath_chroma:/app/rag/chroma_db \
  wanderpath-autonomous-platform:latest
```

---

## 💾 4. Data Persistence & Volume Management

Wanderpath stores all state in persistent Docker volumes so no data is lost when containers restart:

1. **`wanderpath_db_data`** (`/app/db/wanderpath.sqlite3`):
   - Atomic checkpoints for every state graph step.
   - Human-in-the-Loop (HITL) task queue and managerial decisions.
   - Unplanned Failure Recovery tickets and JSON state patches.
2. **`wanderpath_chroma_data`** (`/app/rag/chroma_db`):
   - ChromaDB persistent vector embeddings for consular policies and resort cancellation rules.

### Inspecting Persistent Volumes
```bash
# List volumes
docker volume ls | grep wanderpath

# Backup SQLite database from container
docker cp wanderpath-platform:/app/db/wanderpath.sqlite3 ./backup_wanderpath.sqlite3
```

---

## 🔑 5. Live Real-World LLM Configuration

The platform supports live LLM execution via Google Gemini, OpenAI, or Anthropic Claude.

Configure your API keys in `.env`:
```ini
# Google Gemini (Recommended)
GEMINI_API_KEY=AIzaSy...

# OpenAI
OPENAI_API_KEY=sk-proj-...
OPENAI_MODEL=gpt-4o-mini

# Anthropic Claude
ANTHROPIC_API_KEY=sk-ant-...
ANTHROPIC_MODEL=claude-3-5-sonnet-20241022
```

> **Zero-Dependency Fallback**: If no API keys are configured, the platform runs in high-fidelity deterministic domain mode, guaranteeing 100% functionality without any external network dependencies or credit consumption.

---

## 🌐 6. Real-World Webhook Integrations

The containerized platform includes live production webhook endpoints for external systems to advance state graphs:

### 1. Diplomatic Consular Webhook (`POST /api/webhooks/consular`)
```bash
curl -X POST http://localhost:8500/api/webhooks/consular \
  -H "Content-Type: application/json" \
  -d '{
    "thread_id": "thread-live-visa-101",
    "consular_reference": "CONS-FRA-2026-991",
    "decision": "APPROVED",
    "fee": 650.00,
    "notes": "Emergency fast-track biometrics verified."
  }'
```

### 2. Airline GDS Settlement Webhook (`POST /api/webhooks/gds`)
```bash
curl -X POST http://localhost:8500/api/webhooks/gds \
  -H "Content-Type: application/json" \
  -d '{
    "thread_id": "thread-live-dispute-202",
    "decision": "OFFER_PARTIAL",
    "amount": 200.00,
    "fee_waiver": 350.00,
    "legal_rationale": "EU261 crew scheduling delay accepted."
  }'
```

### 3. Hospital ICU Bed Confirmation (`POST /api/webhooks/hospital`)
```bash
curl -X POST http://localhost:8500/api/webhooks/hospital \
  -H "Content-Type: application/json" \
  -d '{
    "thread_id": "thread-live-medevac-303",
    "bed_confirmed": true,
    "icu_ward": "SGH-ICU-BED-04",
    "attending_physician": "Dr. K. Chen, MD",
    "charter_guarantee": 14500.00
  }'
```

---

## 🧪 7. Running Verification Tests inside Docker

To execute the automated smoke test suite directly inside a fresh Docker container:

```bash
docker run --rm wanderpath-autonomous-platform:latest test
```

Expected Output:
```
================================================================================
🌟 WANDERPATH AUTONOMOUS AGENT SYSTEM — MASTER SMOKE TEST (Final v3.0)
================================================================================
[Concern 1/6] Dynamic MCP Tool Registry                       | PASSED (100%)
[Concern 2/6] Dynamic RAG Policy Store                        | PASSED (100%)
[Concern 3/6] Durable SQLite Checkpointing                    | PASSED (100%)
[Concern 4/6] Three Stateful Agent Graphs                     | PASSED (100%)
[Concern 5/6] HITL & Failure Ticket Recovery                  | PASSED (100%)
[Concern 6/6] Full-Stack Platform API & Frontend              | PASSED (100%)
================================================================================
🎉 ALL 6 ARCHITECTURAL CONCERNS PASSED 100% WITH ZERO DEFECTS!
================================================================================
```

---

## 🛠️ 8. Useful Docker Commands

```bash
# View live container logs
docker compose logs -f wanderpath-platform

# Restart services
docker compose restart

# Stop services without deleting database volumes
docker compose down

# Stop services and remove volumes (Full Reset)
docker compose down -v
```
