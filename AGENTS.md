# AGENTS.md

This file provides guidance to Codex (Codex.ai/code) when working with code in this repository.

## Common commands

Backend commands are run from `backend/` after installing the editable dev package:

```bash
python -m pip install -e "./backend[dev]"
cd backend
python -m pytest
python -m pytest tests/test_pipeline.py
python -m pytest tests/test_pipeline.py::test_name
python -m uvicorn --factory kylinguard.api:create_app --host 127.0.0.1 --port 8000 --reload
```

Frontend commands are run from `frontend/`:

```bash
npm ci
npm run dev
npm run build
npm test
node --test tests/useChat.test.js
```

Full-app and deployment helpers from the repository root:

```bash
cp .env.example .env
./start.sh                       # Docker build + background start

docker compose up -d --build
docker compose ps
docker compose logs -f
docker compose down

sudo ./install.sh                # system install path; writes systemd/sudoers assets
```

`frontend/vite.config.js` proxies `/api` to `http://127.0.0.1:8000` during Vite development. A production frontend build is served by the FastAPI process from `frontend/dist`.

There is no configured lint script in `backend/pyproject.toml` or `frontend/package.json`; do not invent one when validating changes.

## Architecture overview

KylinGuard is a single-user local security operations Agent for Kylin/Linux systems. The product flow is a five-stage loop: perceive system state, plan with an LLM, verify risk, execute tools, and write a replayable provenance/audit trail.

### Backend

The backend is a FastAPI app in `backend/kylinguard/api.py`. It exposes chat/SSE task streaming, confirmations, session permissions, model-provider configuration, audit queries, policy/alert/dashboard endpoints, health checks, and static frontend hosting.

`backend/kylinguard/pipeline.py` is the core orchestrator. It serializes turns per session, reconstructs conversation context from the audit log after restart, emits structured progress/SSE events, records events before exposing them, and loops over model plans until either tools are executed or a final answer is produced. The key collaborators are:

- `planner.py` and `llm.py` for OpenAI-compatible model calls and system prompt construction.
- `reviewer.py`, `rules.py`, `gate.py`, and `intent.py` for static risk screening, intent filtering, reviewer checks, and final allow/confirm/deny decisions.
- `authorization.py`, `permissions.py`, and `sessions.py` for permission modes, trusted-directory grants, action fingerprints, TTLs, and full-access state.
- `audit.py` for the SQLite WAL-backed SHA-256 hash chain. Audit write failure is fatal by design.
- `llm_config.py` for GUI-managed providers/models/defaults/session model routing. API keys are stored as separate restricted files; `.env` no longer configures model providers or keys.

Tools are MCP stdio plugins managed by `backend/kylinguard/mcp_client.py`. Built-in servers live under `backend/kylinguard/plugins/` for sysinfo, services, logs, network, disk, security, structured files, and generic command execution. `registry.py` assigns risk metadata; unknown third-party tools are treated as high risk. `files.*` gives structured path-based file operations, while `run_command.run_command` intentionally exposes a full shell gated by permissions and auditing.

Configuration is read through `backend/kylinguard/config.py` from `KG_*` environment variables and `.env`. Important runtime knobs include `KG_DB_PATH`, `KG_WORKSPACE_ROOT`, `KG_COMMAND_SHELL`, command timeouts, `KG_EXEC_USER`, `KG_PRIVILEGED_HELPER`, and full-access TTL/kill-switch settings.

### Frontend

The frontend is a Vue 3 + Vite app in `frontend/src/`. `App.vue` owns the page shell and switches between task chat, model service settings, audit, policy/security, dashboard, and alerts views.

Stateful frontend behavior is concentrated in composables:

- `useChat.js` opens the chat SSE stream, aggregates backend events into renderable turns, handles assistant streaming, confirmation cards, permission requests, session history, cancellation, and replay.
- `useModels.js` manages provider/model/default/session model state and request payloads.
- `usePermissions.js` manages permission context, trusted roots, full-access draft sessions, grants, revocation, and request resolution.
- `useApi.js` is the thin fetch wrapper used by the other composables.

Reusable rendering lives in `frontend/src/components/`, including confirmation cards, model/permission selectors, rich markdown/mermaid rendering, trace steps, side bar, and system status panel. Tests in `frontend/tests/` are Node test-runner tests for composables and utilities rather than browser tests.

### Data and runtime model

The default local database is `data/kylinguard.db`; Docker overrides it to `/app/data/kylinguard.db` on the `kylinguard-data` volume. Docker also mounts `/workspace` as `KG_WORKSPACE_ROOT`, runs the app as a non-root `kylinguard` user, keeps the root filesystem read-only, and binds port `8000` only to `127.0.0.1`.

A normal WSL/Windows development loop runs the backend on `127.0.0.1:8000` and the Vite frontend on `127.0.0.1:5173`. If `npm run build` has been run, the backend can serve the built frontend directly on port 8000.
