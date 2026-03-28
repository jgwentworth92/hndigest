# ADR-004: LLM Orchestration — Prompt Chains, Retry Logic, Provider Abstraction

## Status: ACCEPTED
## Date: 2026-03-27

---

## Problem

The hndigest pipeline includes two LLM-dependent stages that form a prompt chain: the Summarizer generates a summary from article text, and the Validator checks that summary against the source for faithfulness. These are not independent operations — validation results determine whether the summary is accepted, retried with a tighter prompt, or rejected entirely (SPEC-000 section 5.10).

The current Phase 1 implementation has the LLM adapter (configurable across Gemini, Claude, OpenAI, local endpoints) and externalized prompt templates, but no orchestration logic connecting the two stages. This ADR covers how the prompt chain is coordinated, how retries work, and how the provider abstraction fits into the agent architecture.

---

## Context

### What exists today (end of Phase 1)

| Component | Location | Status |
|---|---|---|
| LLM adapter with 4 providers | `src/hndigest/mcp/llm_mcp.py` | Working, tested against live Gemini |
| Prompt templates | `config/prompts.yaml` | Externalized, two categories (summarizer, validator) |
| Provider config | `config/llm.yaml` | Gemini default, Claude/OpenAI/local configured |
| Summarizer agent | `src/hndigest/agents/summarizer.py` | Empty placeholder |
| Validator agent | `src/hndigest/agents/validator.py` | Empty placeholder |
| Message bus channels | summary, validated_summary | Defined, not yet used |

### The prompt chain

```
article channel
    |
    v
Summarizer agent
    |  calls LLM adapter.generate_summary()
    |  publishes to summary channel
    v
summary channel
    |
    v
Validator agent
    |  calls LLM adapter.validate_summary()
    |  if PASS: publish to validated_summary channel
    |  if FAIL: request retry (once), then reject
    v
validated_summary channel
    |
    v
Report Builder (consumes validated summaries)
```

### The retry problem

SPEC-000 section 5.10 point 6: "If FAIL: flag summary as rejected. Optionally retry summarizer once with a tighter prompt. If second attempt also fails, story gets no summary in the digest."

This requires stateful coordination: the validator must track whether this is a first attempt or a retry, and either trigger re-summarization or give up. Pure message-bus-only orchestration loses this state.

---

## Options Considered

### Option A: Validator owns the full retry loop

| Aspect | Detail |
|---|---|
| How it works | Validator receives summary, validates. On fail, calls `LLMAdapter.generate_summary()` directly with a tighter prompt variant, then validates again. Two LLM calls happen within one `process()` invocation. |
| Pros | Simple, no new agents or channels. All retry state is local to one process() call. |
| Cons | Validator does two jobs (validation + re-summarization). Violates single-responsibility. Makes the validator harder to test independently. |

### Option B: New orchestrator agent

| Aspect | Detail |
|---|---|
| How it works | A new "SummaryOrchestrator" agent sits between the article channel and the validated_summary channel. It calls the summarizer, then the validator, handles retries, and only publishes validated summaries. |
| Pros | Clean separation. Orchestrator owns the state machine. Summarizer and validator stay pure. |
| Cons | Adds an 8th agent. More moving parts. The orchestrator bypasses the message bus for internal coordination (calls LLM adapter directly rather than publishing/subscribing). |

### Option C: Bus-based retry with message metadata

| Aspect | Detail |
|---|---|
| How it works | Validator publishes a "retry_summary" message back to the summary channel (or a dedicated retry channel) with attempt count in metadata. Summarizer picks it up, uses a tighter prompt, re-publishes. Validator sees the retry and either accepts or rejects. |
| Pros | Fully decoupled. Uses existing bus infrastructure. All coordination is via messages. |
| Cons | Distributed state across messages. Harder to reason about. Retry loops could create message cycles if not carefully bounded. Adds complexity to both agents. |

---

## Decision

**Option A: Validator owns the retry loop.** This is the simplest approach that works.

Rationale:
- The retry is a single conditional branch, not a complex state machine. It happens at most once per story.
- The validator already has the source text (it loaded it for validation). Calling `generate_summary()` again with a tighter prompt is one additional LLM call.
- The "tighter prompt" is a second prompt template in `config/prompts.yaml` (category: `summarizer_retry`), keeping it configurable.
- Testing: the validator can be tested end-to-end with a source text that produces a bad summary on first attempt. The retry path is exercised within the same test.
- If the retry pattern becomes more complex in the future (multiple retries, different strategies per category), we can refactor to Option B without changing the external interface (validated_summary channel output stays the same).

### Retry flow within the Validator agent

```
1. Receive summary message from summary channel
2. Load source article text from SQLite
3. Call LLM adapter.validate_summary(summary, source_text)
4. If PASS:
   - Update summary status to "validated" in summaries table
   - Persist validation result to validations table
   - Publish to validated_summary channel
5. If FAIL (first attempt):
   - Persist validation result (fail) to validations table
   - Call LLM adapter.generate_summary() with "summarizer_retry" prompt template
   - Call LLM adapter.validate_summary() on the new summary
   - If PASS: persist new summary, update status to "validated", publish
   - If FAIL: update status to "rejected", persist validation, do not publish
6. Story with no validated summary appears in digest as "No summary available"
```

### Provider abstraction

The LLM adapter is provider-agnostic. Summarizer and validator both use the same adapter instance (or separate instances if different models are desired). Provider selection is via `config/llm.yaml` and `LLM_PROVIDER` env var.

Future option: use a cheaper/faster model for summarization and a more capable model for validation. This is supported by the config — each agent could instantiate its own `LLMAdapter` with a `provider_override`.

### Prompt template categories

| Category | Purpose | Config key in prompts.yaml |
|---|---|---|
| summarizer | First-attempt summary generation | `summarizer.system`, `summarizer.user` |
| summarizer_retry | Tighter retry prompt after validation failure | `summarizer_retry.system`, `summarizer_retry.user` |
| validator | Faithfulness check with structured JSON output | `validator.system`, `validator.user` |

---

## Phase 3 Implementation Plan

### Step 1: Add retry prompt template

- [ ] Add `summarizer_retry` category to `config/prompts.yaml` with a tighter system prompt emphasizing only verifiable claims
- [ ] Verify LLM adapter can load and format the new template

### Step 2: Implement Summarizer agent

- [ ] Implement summarizer in `src/hndigest/agents/summarizer.py`
- [ ] Subscribe to article channel
- [ ] On receiving article message, call `LLMAdapter.generate_summary()`
- [ ] Compute SHA-256 hash of source text
- [ ] Persist summary to summaries table with status "pending_validation"
- [ ] Publish to summary channel with story_id, summary text, and source text hash
- [ ] Skip articles under 100 characters, mark as "no_summary"

### Step 3: Implement Validator agent with retry

- [ ] Implement validator in `src/hndigest/agents/validator.py`
- [ ] Subscribe to summary channel
- [ ] On receiving summary, load source text from articles table
- [ ] Call `LLMAdapter.validate_summary()`
- [ ] On PASS: update summary status, persist validation, publish to validated_summary channel
- [ ] On FAIL (attempt 1): call `generate_summary()` with `summarizer_retry` template, then validate again
- [ ] On FAIL (attempt 2): mark as rejected, persist, do not publish
- [ ] Persist all validation results (pass and fail) to validations table

### Step 4: Register agents with supervisor

- [ ] Add summarizer and validator to the supervisor's agent list in CLI start command
- [ ] Verify both agents boot, emit heartbeats, and respond to shutdown

### Step 5: End-to-end test

- [ ] Test: seed article in database, start summarizer + validator, verify validated summary appears on validated_summary channel
- [ ] Test: use a prompt that produces a deliberately bad summary, verify retry occurs and validation result is recorded
- [ ] Test: full pipeline — collector -> fetcher -> summarizer -> validator with live HN story and live LLM
- [ ] Write artifacts for all LLM test runs

---

## Consequences

- The validator has a dual role: validation + retry orchestration. This is acceptable for a single-retry pattern but should be refactored if retry logic grows more complex.
- Retry prompt is externalized in YAML alongside other prompts. Prompt engineering can iterate without code changes.
- Different providers or models can be used for summarization vs validation by configuring separate adapter instances. This is not the default but is supported.
- The validated_summary channel contract is unchanged: downstream consumers (report builder) receive only validated summaries. The retry is invisible to them.
