# Initial Baseline Snapshot

Date: 2026-06-16
Scope: current project state before the Communication Copilot refactor.

## 1. Project Structure

Top-level directories with active ownership relevance:

- `app/`: FastAPI runtime, orchestration, domain logic, tasking, RAG, mail.
- `web/`: Streamlit user workspace and governance console.
- `spec/`: current design docs, strongest existing subdomain spec is Mail Agent.
- `scripts/`: Docker-only regression, migration, benchmark, and harness scripts.
- `memory/`: bootstrap notes and memory-related guidance.
- `mcp/`: MCP-style tool bridge surfaces.
- `deploy/`: public gateway config.
- `prometheus/`: metrics config.

Key entry points:

- `app/main.py`: HTTP API and runtime composition root.
- `app/task_queue.py`: Celery queues.
- `app/task_worker.py`: worker task handlers.
- `web/streamlit_app.py`: user-facing workspace.
- `web/governance_console.py`: governance UI.

## 2. Tech Stack

- Language: Python
- API framework: FastAPI
- Async/background: Celery + Redis
- Datastores: PostgreSQL, SQLite stores, Chroma
- UI: Streamlit
- Metrics: Prometheus
- Container runtime: Docker Compose
- Retrieval stack: Chroma dense + SQLite FTS5 sparse + BAAI reranker

## 3. Ownership Mapping

- Communication runtime composition: `app/main.py`
- Orchestration and routing: `app/orchestration/*`
- Mail domain: `app/mail/*`, `app/inbound_mail.py`, `app/outbound_delivery.py`
- RAG grounding: `app/enterprise_rag/*`
- Governance and task lifecycle: `app/task_*`, `app/privacy_lab.py`
- Memory substrate: `app/conversation_memory.py`, `app/hermes_*`
- User workspace UI: `web/streamlit_app.py`
- Governance UI: `web/governance_console.py`

## 4. Contract Inventory

Published and stable-enough interfaces:

- `POST /agent/chat`
- `POST /enterprise-rag/query`
- `POST /enterprise-rag/ingest`
- `GET /enterprise-rag/benchmark`
- `GET /admin/*` governance and diagnostics endpoints
- Streamlit workspace on `8511`
- Governance console on `8512` or `/governance` behind the public gateway

Important internal contracts:

- Typed observation payloads
- Actor context propagation
- Mail draft / confirmation / DLP task lifecycle
- EnterpriseRAG evidence and answer observation contract

## 5. Dependency Direction Convention

Preferred direction:

- UI -> API/runtime entry
- Runtime entry -> Supervisor/orchestration
- Supervisor/orchestration -> domain agents / tools
- Domain agents -> stores / providers / RAG / governance boundaries
- Renderer/verifier consume observations, not business templates

## 6. Test System

- Verification baseline: Docker only
- Primary regression layer: `scripts/*_regression.py`
- Additional safety: `python -m compileall -q app scripts`
- Harness emphasis already exists for Mail Agent paths

## 7. Build And Deploy

- The active verification environment is the local Docker Compose runtime.
- A remote Docker deployment existed earlier in the project and remains useful
  as historical evidence, but it is currently offline and not the acceptance
  target for the upcoming refactor.
- Public deployment routes user workspace and governance through `deploy/nginx`
- Models and EnterpriseRAG datasets are mounted into containers

## 8. Known Anti-Patterns

- Residual LeetCode naming and historical identity markers.
- Legacy orchestration/fallback carriers that still exist for compatibility.
- Some hard-coded user-visible wording remains in registry/fallback surfaces.
- Product narrative is still partly chat-first even though the runtime has moved
  toward governed communication workflows.
- RAG and Mail capabilities are mature, but the top-level product spec has been
  weaker than the subdomain specs.

## 9. Last Review Findings

- Date: 2026-06-16
- Reviewer: Codex with user-guided Aegis brainstorming
- Findings:
  - The strongest product direction is a Mail-Agent-centered Communication
    Copilot.
  - The project should remain agent-first.
  - Mixed entry is required, but thread/context is the dominant work object.
  - Mail Agent should become the canonical communication closeout owner.

## 10. Compatibility Boundaries

- Docker-only verification remains mandatory.
- The current acceptance baseline is local Docker Compose until a new remote
  environment is explicitly restored.
- User-visible answer/clarification/draft wording remains LLM-rendered from
  observations.
- Side effects remain gated by permission, confirmation, DLP/governance, and
  auditable task state.
- Enterprise facts continue to require grounded evidence from EnterpriseRAG.
