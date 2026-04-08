# hndigest — Project Conventions

## Language and Runtime

- Pure Python 3.12+. No transpilation, no Cython, no native extensions.
- asyncio for all concurrency. No threads, no multiprocessing.
- Entry point: `python -m hndigest`

## Environment

- Never run `pip install` directly on the host. All package installs must happen inside Docker containers or an activated virtual environment.
- Use Docker or `docker compose` for running the application and tests.
- For syntax/import checks without running, use `python -c "import ast; ast.parse(open('file.py').read())"`.

## Dependencies

Only these external packages are permitted:

| Package | Purpose |
|---|---|
| aiohttp or httpx | Async HTTP client |
| pydantic | Typed models for message payloads and config validation |
| PyYAML | Category rules and scoring config |
| FastAPI + uvicorn | API server and dashboard |
| readability-lxml or trafilatura | Article text extraction |

Everything else comes from the Python standard library (asyncio, sqlite3, json, hashlib, logging, pathlib, argparse).

## Persistence

- SQLite via stdlib sqlite3. No ORM. No migration framework.
- Schema defined in SQL files under `db/migrations/`, executed in order on startup.
- All timestamps stored as ISO 8601 UTC strings.

## Concurrency Model

- Nine agents run as independent `asyncio.Task` instances managed by a supervisor: collector, orchestrator, fetcher, categorizer, scorer, summarizer, validator, report-builder, chat.
- Message bus: dict of `asyncio.Queue` per channel. Agents publish/subscribe by channel name.
- No shared mutable state between agents. All inter-agent communication goes through the bus.
- The orchestrator gates expensive operations (fetch, summarize) behind priority thresholds and a daily token budget. Cheap operations (categorize, score) run on every story.

## MCP Servers

- Five MCP servers: hn-mcp, web-mcp, sqlite-mcp, llm-mcp, analytics-mcp.
- Each server lives in `src/hndigest/mcp/` as its own module.
- Agents interact with external systems exclusively through MCP tool interfaces.
- analytics-mcp provides 12 query tools for the chat agent's ReAct loop.

## Configuration

- YAML files in `config/` for category rules, scoring weights, LLM provider settings, and prompt templates.
- No config in code. Changing categories, scoring weights, prompts, or LLM provider requires only YAML/.env edits.
- Prompt templates live in `config/prompts.yaml` with placeholder variables. Never hardcode prompts in Python.

## Project Structure

- Source code: `src/hndigest/`
- Tests: `tests/`
- Specs: `specs/` (prose only, never code)
- ADRs: `docs/adr/` (prose and tables only, never code)
- Database migrations: `db/migrations/`
- Config: `config/`
- Output: `output/digests/` (generated markdown digest files, gitignored)

## Code Style

- Type hints on all function signatures.
- Logging via stdlib `logging`. No print statements.
- Docstrings on public classes and functions (Google style).
- No classes where a function suffices.

## Testing

- All tests are end-to-end. No mocking.
- Test runner: pytest with asyncio support.
- **No tests may be skipped.** If a test requires env vars, API keys, network access, or any external setup, ask the user to provide the required configuration before running tests. A skipped test is a failing test.
- **Test artifacts:** Tests that call external APIs (HN, LLM) must write JSON artifacts to `tests/artifacts/` with timestamped filenames. Artifacts record the input data, provider/model used, responses, and validation results so runs can be inspected after the fact. Artifacts are gitignored.
- **Integration tests required:** Every inter-agent message contract must have an integration test that uses the real publisher's output as input to the subscriber. Never rely solely on hand-crafted payloads — they can mask key mismatches. See `tests/test_integration.py`.

## Documentation Rules

- Specs (`specs/`) contain requirements and architecture in prose, tables, and diagrams. Never code.
- ADRs (`docs/adr/`) describe decisions in plain language and tables. Never code. Use phased checklists with checkboxes for implementation plans.
- **Every implementation phase requires an ADR before any code is written.** No exceptions. The ADR must cover: problem, options considered, decision, and phased implementation plan with checkboxes.
- README files provide orientation for their directory. Keep them brief.

## Git

- Never include "Co-Authored-By" lines in commit messages.
