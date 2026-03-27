# hndigest

Multi-agent Hacker News daily digest system. Collects stories from the HN API, fetches articles, categorizes and scores them by signal strength, generates validated LLM summaries, and assembles structured daily digests.

## Architecture

Seven async agents communicate via an in-process message bus:

| Agent | Purpose | LLM |
|---|---|---|
| Collector | Polls HN API, persists stories | No |
| Fetcher | Retrieves article text from URLs | No |
| Categorizer | Assigns topic categories via rules | No |
| Scorer | Ranks stories by signal velocity | No |
| Summarizer | Generates 2-3 sentence summaries | Yes |
| Validator | Checks summaries against source text | Yes |
| Report Builder | Assembles daily digest | No |

## Tech Stack

- Python 3.12+, asyncio, SQLite, aiohttp
- Configurable LLM: Gemini (default), Claude, OpenAI, local endpoints
- YAML config for scoring weights, category rules, prompt templates
- No frameworks beyond FastAPI (Phase 4)

## Quick Start

```bash
# Install
pip install -e ".[dev]"

# Set up .env (copy and fill in API keys)
cp .env.example .env

# Run
python -m hndigest start

# Check status
python -m hndigest status

# View today's stories
python -m hndigest stories --today
```

## Configuration

| File | Purpose |
|---|---|
| `config/scoring.yaml` | Scoring weights and decay thresholds |
| `config/prompts.yaml` | LLM prompt templates (editable without code changes) |
| `config/llm.yaml` | LLM provider and model settings |
| `.env` | API keys (gitignored) |

## Testing

```bash
python -m pytest tests/ -v
```

All tests are end-to-end with no mocking. Tests hit real APIs (HN, Gemini).

## Project Structure

```
src/hndigest/
├── agents/        # Collector, scorer, and base class
├── bus/           # asyncio message bus
├── supervisor/    # Agent lifecycle management
├── mcp/           # HN API client, LLM adapter
├── cli/           # Command-line interface
├── db/            # Migration runner
└── api/           # FastAPI server (Phase 4)
config/            # YAML config files
db/migrations/     # SQL schema files
tests/             # End-to-end tests
docs/adr/          # Architecture decision records
specs/             # SPEC-000 master spec
```

## Implementation Status

- [x] **Phase 1:** Foundation — schema, bus, agents, supervisor, CLI, LLM adapter
- [ ] **Phase 2:** Content pipeline — fetcher, categorizer, report builder
- [ ] **Phase 3:** LLM integration — summarizer, validator with retry
- [ ] **Phase 4:** Interface — FastAPI, WebSocket, dashboard, Docker
