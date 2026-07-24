# Repository Guidelines

## Project Structure & Module Organization

This repository is a Python agent backend for Glodex. Core source lives in `app/`.
Key areas are `app/agent/` for AgentLoop orchestration, `app/tools/` for LangChain
tools, `app/api/` for FastAPI and AGUI WebSocket endpoints, `app/memory/` for
short- and long-term memory, `app/ingest/` and `app/recall/` for data ingestion
and retrieval, and `app/utils/` for shared helpers. Tests live in `tests/`.
Architecture and phase documents live in `docs/`. Runtime output and uploaded
artifacts are kept under `output/` and `uploaded/`.

## Build, Test, and Development Commands

Use the local virtual environment when available:

```powershell
.\.venv\Scripts\python.exe -m compileall app tests
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m uvicorn app.api.server:app --reload
docker compose up -d
```

`compileall` catches syntax/import-time issues. `pytest` runs the unit tests.
`uvicorn` starts the FastAPI API locally. `docker compose up -d` starts configured
service dependencies when the compose stack is needed.

## Coding Style & Naming Conventions

Use Python 3 style with 4-space indentation, type hints for public functions, and
small async functions for agent/tool workflows. Prefer descriptive snake_case for
modules, functions, variables, and test files. Keep tool names stable because they
are surfaced to the agent and AGUI monitor events. When editing files containing
Chinese text on Windows, write UTF-8 and avoid PowerShell here-strings; prefer
`apply_patch` or `node fs.writeFileSync(path, content, 'utf8')`.

## Testing Guidelines

Tests use `pytest`. Add focused tests under `tests/test_*.py` for memory,
middleware, API, and tool behavior. For agent changes, at minimum run compile
checks plus the relevant focused tests, for example:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_memory_compressor.py -q
```

## Commit & Pull Request Guidelines

Recent history uses concise messages such as `feat: upgrade short-term memory
pipeline` and `merge: agent memory module upgrade`. Prefer `<type>: <summary>`
with `feat`, `fix`, `docs`, `test`, or `refactor`. PRs should include a clear
description, affected modules, verification commands, linked issues when
available, and screenshots or event traces for AGUI/frontend changes.

## Security & Configuration Tips

Do not commit `.env`, credentials, API keys, or generated runtime output. Keep
`.env.example` updated when adding configuration. Treat tool output, uploaded
files, and memory/session data as potentially sensitive.
