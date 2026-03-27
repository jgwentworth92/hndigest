# hndigest — Project Conventions

## Language and Runtime

- Pure Python 3.12+. No transpilation, no Cython, no native extensions.
- asyncio for all concurrency. No threads, no multiprocessing.
- Entry point: `python -m hndigest`

## Dependencies

Only these external packages are permitted:

| Package | Purpose |
|---|---|
| aiohttp or httpx | Async HTTP client |
| PyYAML | Category rules and scoring config |
| FastAPI + uvicorn | API server and dashboard |
| readability-lxml or trafilatura | Article text extraction |

Everything else comes from the Python standard library (asyncio, sqlite3, json, hashlib, logging, pathlib, argparse).

## Persistence

- SQLite via stdlib sqlite3. No ORM. No migration framework.
- Schema defined in SQL files under `db/migrations/`, executed in order on startup.
- All timestamps stored as ISO 8601 UTC strings.

## Concurrency Model

- Seven agents run as independent `asyncio.Task` instances managed by a supervisor.
- Message bus: dict of `asyncio.Queue` per channel. Agents publish/subscribe by channel name.
- No shared mutable state between agents. All inter-agent communication goes through the bus.

## MCP Servers

- Four MCP servers: hn-mcp, web-mcp, sqlite-mcp, llm-mcp.
- Each server lives in `src/hndigest/mcp/` as its own module.
- Agents interact with external systems exclusively through MCP tool interfaces.

## Configuration

- YAML files in `config/` for category rules and scoring weights.
- No config in code. Changing categories or scoring weights requires only YAML edits.

## Project Structure

- Source code: `src/hndigest/`
- Tests: `tests/`
- Specs: `specs/` (prose only, never code)
- ADRs: `docs/adr/` (prose and tables only, never code)
- Database migrations: `db/migrations/`
- Config: `config/`

## Code Style

- Type hints on all function signatures.
- Logging via stdlib `logging`. No print statements.
- Docstrings on public classes and functions (Google style).
- No classes where a function suffices.

## Testing

- All tests are end-to-end. No mocking.
- Tests that need network access or LLM API keys are gated by env vars and skip cleanly.
- Test runner: pytest with asyncio support.

## Documentation Rules

- Specs (`specs/`) contain requirements and architecture in prose, tables, and diagrams. Never code.
- ADRs (`docs/adr/`) describe decisions in plain language and tables. Never code. Use phased checklists with checkboxes for implementation plans.
- README files provide orientation for their directory. Keep them brief.

## Git

- Never include "Co-Authored-By" lines in commit messages.
