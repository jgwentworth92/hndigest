# ADR-005: Pydantic Type Safety Retrofit

## Status: ACCEPTED
## Date: 2026-03-31

---

## Problem

The system passes data between agents as untyped Python dicts. Message payloads, config files, and agent constructors all use `dict[str, Any]` with string keys accessed via bracket notation. This caused multiple production bugs:

- **Payload key mismatch** (ADR-002 Amendment 2): collector published `story_id`, scorer/orchestrator read `id`. KeyError at runtime. Tests didn't catch it because they constructed their own payloads with the same wrong key.
- **System channel crash** (ADR-001 Amendment 5): heartbeat messages reached `process()` which tried to extract `story_id` from a heartbeat payload. No type checking prevented this.
- **Config access errors**: raw `config.get("key")` returns None on typos, silently using wrong defaults.

Pydantic models would catch all three classes of bugs at construction time, not at runtime deep in agent logic.

---

## Options Considered

### Migration strategy

| Option | Pros | Cons |
|---|---|---|
| Enforce immediately, update everything in one branch | Clean cut, no mixed patterns, catches all mismatches | Large diff, all tests must update |
| Transition period (accept dicts and models) | Incremental migration | Defeats the purpose — mismatches remain possible during transition |
| Forward-only (new code uses Pydantic, old code stays) | Smallest change | Two patterns forever, existing bugs remain |

**Decision:** Enforce immediately. One branch updates all agents, all configs, all tests, and the bus. No transition period. The whole point is eliminating untyped dict access.

### Scope

| Area | Model | Fields |
|---|---|---|
| **Message payloads** | | |
| Bus message envelope | `BusMessage` | type, timestamp, source, payload (generic) |
| Story published by collector | `StoryPayload` | story_id, title, url, hn_text, score, comments, author, posted_at, hn_type, endpoints |
| Score published by scorer | `ScorePayload` | story_id, composite, components (ScoreComponents) |
| Score components | `ScoreComponents` | score_velocity, comment_velocity, front_page_presence, recency |
| Category published by categorizer | `CategoryPayload` | story_id, categories (list of CategoryAssignment) |
| Category assignment | `CategoryAssignment` | category, method |
| Fetch request from orchestrator | `FetchRequestPayload` | story_id, url, hn_text, title, priority |
| Article published by fetcher | `ArticlePayload` | story_id, text, text_hash, fetch_status |
| Summarize request from orchestrator | `SummarizeRequestPayload` | story_id, priority |
| Summary published by summarizer | `SummaryPayload` | story_id, summary_text, source_text_hash |
| Validated summary from validator | `ValidatedSummaryPayload` | story_id, summary_text, validation_result |
| Orchestrator decision | `OrchestratorDecisionPayload` | story_id, decision, reason, priority_score, budget_remaining |
| **Config classes** | | |
| Scoring config | `ScoringConfig` | weights, recency_decay, front_page_scale, baseline_days |
| Orchestrator config | `OrchestratorConfig` | priority (thresholds), budget, llm (use_llm flag) |
| LLM config | `LLMConfig` | provider, gemini/claude/openai/local settings, max_tokens, temperature, timeout, retry |
| Categories config | `CategoriesConfig` | categories (dict of category rules), hn_type_mappings, default_category |
| Prompts config | `PromptsConfig` | summarizer, summarizer_retry, validator, orchestrator_relevance templates, max_article_chars |

### Where models live

All models in `src/hndigest/models.py` (single file). Config models in `src/hndigest/config.py`. This avoids circular imports — agents import models, models don't import agents.

### Bus enforcement

The `MessageBus.publish()` method accepts `BusMessage` instances only. The `BusMessage.payload` field is typed as a union of all payload models. Subscribers receive `BusMessage` instances with typed payloads. No raw dicts pass through the bus.

### Agent changes

Each agent's `process()` method receives a `BusMessage` and can check `isinstance(message.payload, StoryPayload)` or match on `message.type`. The `BaseAgent.publish()` helper constructs `BusMessage` from a payload model.

---

## Phase 5 Implementation Plan

### Step 1: Add pydantic dependency

- [ ] Add `pydantic` to `pyproject.toml` dependencies
- [ ] Update CLAUDE.md dependencies table

### Step 2: Create payload models

- [ ] Create `src/hndigest/models.py` with all payload models listed above
- [ ] All models extend `pydantic.BaseModel` with `model_config = ConfigDict(frozen=True)`
- [ ] BusMessage has: type (str), timestamp (datetime), source (str), payload (union of all payload types)

### Step 3: Create config models

- [ ] Create `src/hndigest/config.py` with all config models
- [ ] Each config model has a `from_yaml(path)` class method that loads and validates
- [ ] Validation errors on startup are clear and actionable

### Step 4: Update message bus

- [ ] `MessageBus.publish()` accepts `BusMessage` only (not raw dicts)
- [ ] `MessageBus.subscribe()` returns `asyncio.Queue[BusMessage]`
- [ ] Remove `_validate_message()` dict validator — Pydantic handles this
- [ ] Update `_REQUIRED_MESSAGE_KEYS` validation to use Pydantic

### Step 5: Update BaseAgent

- [ ] `BaseAgent.process()` signature changes to `(self, channel: str, message: BusMessage)`
- [ ] `BaseAgent.publish()` helper accepts a payload model and msg_type, constructs BusMessage
- [ ] Heartbeat uses a `HeartbeatPayload` model

### Step 6: Update all agents

- [ ] Collector: publish `StoryPayload`
- [ ] Scorer: receive `StoryPayload`, publish `ScorePayload`
- [ ] Categorizer: receive `StoryPayload`, publish `CategoryPayload`
- [ ] Orchestrator: receive `StoryPayload`/`ScorePayload`/`ArticlePayload`, publish `FetchRequestPayload`/`SummarizeRequestPayload`
- [ ] Fetcher: receive `FetchRequestPayload`, publish `ArticlePayload`
- [ ] Summarizer: receive `SummarizeRequestPayload`, publish `SummaryPayload`
- [ ] Validator: receive `SummaryPayload`, publish `ValidatedSummaryPayload`
- [ ] Report builder: query DB directly (no payload changes needed)

### Step 7: Update all configs

- [ ] Scorer loads `ScoringConfig` instead of raw YAML dict
- [ ] Orchestrator loads `OrchestratorConfig`
- [ ] LLM adapter loads `LLMConfig`
- [ ] Categorizer loads `CategoriesConfig`
- [ ] LLM adapter loads `PromptsConfig`

### Step 8: Update all tests

- [ ] All test payloads use Pydantic models instead of raw dicts
- [ ] Integration tests verify typed payloads flow through the pipeline
- [ ] Any test constructing a raw dict for a bus message must use the model instead
- [ ] Add test: publishing a raw dict to the bus raises TypeError

### Step 9: Verify

- [ ] All 28 existing tests pass with Pydantic enforcement
- [ ] Run `hndigest start` and verify live pipeline works
- [ ] Generate a digest and verify output

---

## Consequences

- Pydantic becomes a runtime dependency (not just dev). It's well-maintained and widely used.
- Every agent change that modifies a payload field requires updating the corresponding model. This is the point — it makes contract changes explicit and catches mismatches at import/construction time.
- Config validation errors surface at startup instead of deep in agent logic. A typo in scoring.yaml is caught when the supervisor boots, not when the first story is scored.
- The bus no longer accepts arbitrary dicts. Any code that publishes raw dicts will get a TypeError immediately.
- Test payloads must use models. This prevents the class of bug where tests construct payloads with different keys than the real publisher.
