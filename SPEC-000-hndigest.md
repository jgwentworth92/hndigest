# SPEC-000: hndigest — Multi-Agent Hacker News Daily Digest System

## Status: DRAFT
## Date: 2026-03-27

---

## 1. Problem Statement

The volume of technology news, tools, papers, and discussions published daily exceeds what any individual can consume. Hacker News surfaces hundreds of stories per day across its top, new, best, show, ask, and job endpoints. Determining what is worth reading requires manually scanning titles, clicking through articles, and making judgment calls about relevance, all of which takes hours.

hndigest collects stories from the Hacker News API, fetches their source articles, categorizes and ranks them by measurable signal strength, generates concise summaries, validates those summaries against source text, and assembles a structured daily digest.

---

## 2. Design Principles

1. **Deterministic by default.** Collection, deduplication, categorization (first pass), scoring, and report assembly are all deterministic. The system produces a useful ranked and categorized feed without any LLM calls.
2. **LLM is isolated and validated.** Three agents may use an LLM: the Summarizer, the Validator, and the Orchestrator (optional LLM mode). Four agents may use an LLM when the Chat agent is included. Every summary is checked against its source article before inclusion in the digest.
3. **Every summary is traceable.** Each digest entry links back to the HN story, the source article text, and the raw metadata used for scoring and categorization.
4. **Minimal dependencies.** Pure Python, asyncio, SQLite, MCP.
5. **Test without tokens.** The deterministic pipeline (collection through scoring) is fully testable without LLM calls or network access.

---

## 3. Dependencies

| Dependency | Purpose |
|---|---|
| Python 3.12+ | Runtime |
| asyncio (stdlib) | Agent concurrency, message bus |
| sqlite3 (stdlib) | Persistence |
| aiohttp or httpx | Async HTTP for HN API and article fetching |
| PyYAML | Category rules configuration |
| FastAPI + uvicorn | API server and dashboard |
| readability-lxml or trafilatura | Article text extraction from HTML |

---

## 4. Data Source

Single source: Hacker News API (https://github.com/HackerNews/API).

| Endpoint | Data | Update Frequency |
|---|---|---|
| /v0/topstories | Top 500 story IDs | Near real-time |
| /v0/newstories | Newest 500 story IDs | Near real-time |
| /v0/beststories | Best 500 story IDs | Near real-time |
| /v0/showstories | Show HN story IDs | Near real-time |
| /v0/askstories | Ask HN story IDs | Near real-time |
| /v0/jobstories | Job posting IDs | Near real-time |
| /v0/item/{id} | Individual item: title, URL, score, descendants (comment count), by (author), time, type | Per-item |

Auth: None. Rate limits: Undocumented but generous. Firebase-backed REST API.

All data is structured: integers (score, comment count, timestamp), strings (title, URL, author), and enums (type: story, job, comment, ask, show).

---

## 5. Agent Architecture

Nine agent instances. Each is an independent, long-running async task. Agents communicate through an in-process async message bus (`asyncio.Queue` per channel).

### 5.1 Message Bus

Dict of `asyncio.Queue` objects, one per channel. Agents call `bus.publish(channel, message)` and `bus.subscribe(channel)`. Abstract interface allows future swap to Redis pub/sub or NATS with no agent code changes.

### 5.2 Channels

| Channel | Publisher | Subscriber(s) | Message Content |
|---|---|---|---|
| story | Collector | Orchestrator, Categorizer, Scorer | Story metadata (id, title, url, score, comments, author, timestamp, hn_type) |
| fetch_request | Orchestrator | Fetcher | Story ID + priority + reason for fetch |
| article | Fetcher | Summarizer (via summarize_request) | Story ID + extracted article text |
| summarize_request | Orchestrator | Summarizer | Story ID + priority + article text reference |
| category | Categorizer | Report Builder | Story ID + assigned categories |
| score | Scorer | Orchestrator, Report Builder | Story ID + computed signal score + component breakdown |
| summary | Summarizer | Validator | Story ID + generated summary + source text hash |
| validated_summary | Validator | Report Builder | Story ID + validated summary (or rejection flag) |
| digest | Report Builder | WebSocket broadcast | Completed daily digest |
| chat_request | API / CLI | Chat Agent | User query + session ID |
| chat_response | Chat Agent | API / CLI | Agent response + tool calls log + session ID |
| system | Supervisor | All agents | Lifecycle commands (shutdown, pause, health_check) |

### 5.3 Agent Internal Structure

Every agent follows the same pattern:

```
Agent
├── identity (name, version)
├── subscriptions (list of channels to watch)
├── publications (list of channels it writes to)
├── tools (list of MCP tool references)
├── state (persisted to SQLite, loaded on start)
├── run loop:
│   ├── receive message from subscribed channel
│   ├── process message (agent-specific logic)
│   ├── persist results to SQLite
│   ├── publish output message to downstream channel
│   └── emit heartbeat
└── shutdown handler (drain queue, persist final state)
```

### 5.4 Supervisor

Process manager. Not an LLM agent.

- Start all agents on system boot
- Monitor agent health via heartbeat (every 30 seconds)
- Restart failed agents with exponential backoff (max 3 retries, then alert operator)
- Graceful shutdown: publish shutdown to system channel, wait for agents to drain, terminate
- Expose agent status to the API layer

### 5.5 Collector Agent

**Purpose:** Poll HN API endpoints, fetch individual story metadata, track score and comment count over time, publish to story channel, persist to SQLite.

**Behavior:**
1. Poll top/new/best/show/ask/job endpoints every 10 minutes
2. For each story ID not already in the database, fetch item details from /v0/item/{id}
3. For stories already tracked, update score and comment count (for velocity calculation)
4. Publish new stories to the story channel
5. Deduplicate: a story appearing on both top and best is stored once

**Toolset (via MCP):** HN API client, SQLite write (stories table).

**LLM involvement:** None.

### 5.6 Fetcher Agent

**Purpose:** Retrieve article text from story URLs. Handle the messiness of the web: paywalls, PDFs, dead links, non-English content.

**Behavior:**
1. Receive fetch_request message from fetch_request channel
2. If story has no URL (Ask HN, some jobs), use the HN text field instead. Publish to article channel.
3. Fetch URL with timeout (10 seconds)
4. Extract article text using readability/trafilatura
5. If fetch fails (paywall, 404, timeout), mark story as fetch_failed. Do not retry.
6. Publish extracted text to article channel
7. Persist article text to SQLite

**Concurrency:** Multiple fetches run in parallel (up to 10 concurrent requests). Each fetch is an independent async task.

**Toolset (via MCP):** HTTP client, article text extractor, SQLite write (articles table).

**LLM involvement:** None.

### 5.7 Categorizer Agent

**Purpose:** Assign topic categories to each story.

**Behavior:**
1. Receive story message from story channel
2. Run deterministic categorization:
   - URL domain mapping (github.com = tools, arxiv.org = research, news sites = industry, etc.)
   - Title keyword matching against category rules defined in YAML config
   - HN type mapping (show = launches, ask = discussion, job = jobs)
3. If deterministic pass assigns at least one category, publish and persist
4. If no category matched, assign "uncategorized"

**Category taxonomy (defined in YAML config):**

| Category | Signals |
|---|---|
| ai-ml | Keywords: llm, gpt, transformer, neural, machine learning, deep learning, agent, rag. Domains: arxiv.org, huggingface.co, openai.com |
| web-dev | Keywords: react, vue, nextjs, css, browser, frontend, backend, api. Domains: vercel.com, developer.mozilla.org |
| devops-infra | Keywords: kubernetes, docker, aws, terraform, ci/cd, deploy, cloud, linux. Domains: cloud.google.com, aws.amazon.com |
| languages | Keywords: rust, python, go, typescript, zig, elixir, compiler, type system |
| tools | Keywords: cli, editor, ide, database, library, framework, sdk. HN type: show |
| security | Keywords: vulnerability, cve, breach, encryption, authentication, zero-day |
| career | Keywords: hiring, interview, salary, remote, layoff, startup. HN type: job |
| research | Keywords: paper, study, proof, theorem, algorithm. Domains: arxiv.org, dl.acm.org, nature.com |
| business | Keywords: funding, acquisition, ipo, revenue, valuation, startup |
| culture | Anything not matching the above patterns that appeared on HN front page |

New categories and keyword rules are added by editing the YAML config. No code changes required.

**Toolset (via MCP):** SQLite read (stories), SQLite write (categories table), category rules config.

**LLM involvement:** None.

### 5.8 Scorer Agent

**Purpose:** Rank stories by measurable signal strength using score velocity, comment velocity, and front page presence.

**Behavior:**
1. Receive story message from story channel
2. Compute signal components:
   - **Score velocity:** points per hour since posted
   - **Comment velocity:** comments per hour since posted
   - **Front page presence:** number of endpoints story appears on (top, best, show, etc.)
   - **Recency weight:** exponential decay favoring stories from today
3. Compute composite signal score (0-100) from weighted components
4. Publish to score channel
5. Persist to SQLite

**Scoring weights (defined in YAML config):**

| Component | Weight | Calculation |
|---|---|---|
| Score velocity | 0.35 | points / hours_since_posted, normalized to 0-100 via percentile rank against trailing 7-day baseline |
| Comment velocity | 0.30 | comments / hours_since_posted, normalized same way |
| Front page presence | 0.20 | count of HN endpoints story appears on, scaled: 1 endpoint = 20, 2 = 50, 3+ = 100 |
| Recency | 0.15 | Exponential decay: stories from last 6 hours = 100, 12h = 70, 24h = 40, 48h = 10 |

**Toolset (via MCP):** SQLite read (stories, historical scores for baseline), SQLite write (scores table).

**LLM involvement:** None.

### 5.9 Summarizer Agent

**Purpose:** Generate a 2-3 sentence summary of each article.

**Behavior:**
1. Receive article message from article channel
2. If article text is under 100 characters (fetch failed, empty page), skip. Mark as no_summary.
3. Construct prompt with article text and story title as context
4. Call LLM via MCP tool
5. Publish summary + source text hash to summary channel
6. Persist to SQLite

**Prompt constraints:**
- Summary must be 2-3 sentences
- Summary must only describe what the article says
- No opinions, no speculation, no information not present in the article

**Toolset (via MCP):** SQLite read (articles), SQLite write (summaries table), LLM API.

**LLM involvement:** Yes.

### 5.10 Validator Agent

**Purpose:** Check that each summary is faithful to the source article. Reject summaries that hallucinate.

**Behavior:**
1. Receive summary message from summary channel
2. Load source article text from SQLite using the story ID
3. Construct validation prompt: given the source article and the summary, does every claim in the summary appear in the source? Respond with PASS or FAIL plus specific citation for each claim.
4. Call LLM via MCP tool
5. If PASS: publish validated summary to validated_summary channel
6. If FAIL: flag summary as rejected. Optionally retry summarizer once with a tighter prompt. If second attempt also fails, story gets no summary in the digest.
7. Persist validation result to SQLite

**Toolset (via MCP):** SQLite read (summaries, articles), SQLite write (validations table), LLM API.

**LLM involvement:** Yes.

### 5.11 Report Builder Agent

**Purpose:** Assemble the daily digest from categories, scores, and validated summaries.

**Behavior:**
1. Triggered on schedule (configurable, default: every 6 hours) or on demand via CLI/API
2. Query SQLite for all stories from the current period
3. Join with categories, scores, and validated summaries
4. Group stories by category
5. Within each category, rank by composite signal score
6. Apply configurable limits: top N stories per category (default: 5), top M categories (default: all with stories)
7. Format digest as structured JSON and as rendered markdown
8. Publish to digest channel (triggers WebSocket broadcast)
9. Persist to SQLite

**Output format per story:**

```
- Title (linked to source URL)
- HN discussion link
- Signal score
- Category tags
- Summary (if available, "No summary available" if fetch failed or validation rejected)
- Score: X points | Comments: Y | Posted: Z hours ago
```

**Toolset (via MCP):** SQLite read (all tables), SQLite write (digests table), template engine.

**LLM involvement:** None.

### 5.12 Orchestrator Agent

**Purpose:** Control which stories are fetched and summarized based on priority thresholds and a daily token budget. Sits between the collector and downstream agents (fetcher, summarizer). Logs all dispatch decisions.

**Behavior:**
1. Subscribe to story channel (receives all new stories from collector) and score channel (receives composite scores)
2. For each story, evaluate priority using composite score and category
3. Apply priority threshold: stories below the threshold are logged as "skipped" and not dispatched for fetching
4. Track daily token budget: estimate token cost per article fetch + summarization, deduct from budget when dispatching
5. If budget is exhausted, log as "budget_exceeded" and stop dispatching until budget resets (midnight UTC)
6. For stories above threshold with available budget: publish fetch_request to fetch_request channel
7. When article is fetched (monitor article channel), publish summarize_request to summarize_request channel
8. Log every decision to orchestrator_decisions table: story_id, decision (dispatched, skipped, budget_exceeded), reason, priority_score, budget_remaining, decided_at
9. Optional LLM mode: for stories near the priority threshold (ambiguous cases), call LLM to make a relevance judgment. Controlled by config flag `orchestrator.use_llm` (default: false, pure rules-based)

**Priority thresholds (defined in YAML config):**

| Setting | Default | Description |
|---|---|---|
| min_composite_score | 30 | Minimum composite score to dispatch for fetch |
| daily_token_budget | 100000 | Estimated tokens available per day for fetch + summarize |
| tokens_per_article | 1000 | Estimated token cost per article (fetch + summarize + validate) |
| ambiguity_range | 5 | Score range around threshold where LLM mode activates (e.g., 25-35) |

**Toolset (via MCP):** SQLite read (stories, scores), SQLite write (orchestrator_decisions table), LLM API (optional).

**LLM involvement:** Optional (controlled by config flag).

### 5.13 Chat Agent

**Purpose:** Provide a conversational interface for querying collected data using a ReAct (Reason + Act) loop. The chat agent thinks step-by-step, selects tools from the analytics-mcp server, executes them, and synthesizes results into natural language responses.

**Behavior:**
1. Receive chat_request message from chat_request channel (from CLI or API)
2. Load conversation history from chat_sessions and chat_messages tables
3. Enter ReAct loop:
   a. Reason: analyze the user query and available tool results
   b. Act: select and call a tool from analytics-mcp, or generate a final response
   c. Observe: process tool results
   d. Repeat until the agent has enough information to answer
4. Persist the conversation turn (user message + agent response + tool calls) to chat_messages table
5. Publish response to chat_response channel

**Available tools (via analytics-mcp):**

| Tool | Description |
|---|---|
| query_stories | Search stories by keyword, category, date range, score threshold |
| query_digests | Search digests by date range |
| get_story_detail | Full story data: metadata, article, summary, validation, score |
| category_breakdown | Count of stories per category for a date range |
| trending_categories | Categories with increasing story volume over time |
| top_stories | Top N stories by composite score for a date range |
| score_distribution | Histogram of composite scores for a date range |
| author_activity | Stories and scores for a specific HN author |
| keyword_trend | How often a keyword appears in titles over time |
| daily_volume | Number of stories collected per day |
| compare_days | Side-by-side comparison of two days' metrics |
| system_status | Agent statuses, uptime, message counts |

**Conversation persistence:**
- Each conversation has a session_id (UUID)
- Messages are stored in order with role (user/assistant), content, tool calls, and timestamps
- Sessions can be resumed by session_id

**Access points:**
- CLI: `hndigest chat "what were the top AI stories today?"`
- REST: POST /api/chat with {session_id, message}
- Web dashboard: chat view with streaming responses

**Toolset (via MCP):** analytics-mcp (all 12 tools above), SQLite read/write (chat tables), LLM API.

**LLM involvement:** Yes (always — the ReAct loop requires an LLM).

---

## 6. Data Model

Single SQLite database.

### stories

| Column | Type | Description |
|---|---|---|
| id | INTEGER PK | HN item ID (use HN's own ID, no auto-increment) |
| title | TEXT | Story title |
| url | TEXT | Source URL (nullable for Ask HN) |
| hn_text | TEXT | HN post text for Ask HN / Show HN descriptions (nullable) |
| score | INTEGER | Current score (updated on each poll) |
| comments | INTEGER | Current comment count (updated on each poll) |
| author | TEXT | HN username |
| posted_at | TIMESTAMP | When originally posted (from HN `time` field) |
| hn_type | TEXT | "story", "show", "ask", "job" |
| endpoints | JSON | Which HN endpoints this story appeared on |
| first_seen | TIMESTAMP | When collector first found this story |
| last_updated | TIMESTAMP | Last time score/comments were updated |

### score_snapshots

| Column | Type | Description |
|---|---|---|
| id | INTEGER PK | Auto-increment |
| story_id | INTEGER | FK to stories |
| score | INTEGER | Score at this snapshot |
| comments | INTEGER | Comment count at this snapshot |
| snapshot_at | TIMESTAMP | When snapshot was taken |

### articles

| Column | Type | Description |
|---|---|---|
| id | INTEGER PK | Auto-increment |
| story_id | INTEGER | FK to stories |
| text | TEXT | Extracted article text |
| text_hash | TEXT | SHA-256 of the extracted text |
| fetch_status | TEXT | "success", "failed", "paywall", "timeout", "no_url" |
| fetched_at | TIMESTAMP | When fetched |

### categories

| Column | Type | Description |
|---|---|---|
| id | INTEGER PK | Auto-increment |
| story_id | INTEGER | FK to stories |
| category | TEXT | Assigned category tag |
| method | TEXT | "domain", "keyword", "hn_type", "uncategorized" |
| categorized_at | TIMESTAMP | When categorized |

### scores

| Column | Type | Description |
|---|---|---|
| id | INTEGER PK | Auto-increment |
| story_id | INTEGER | FK to stories |
| score_velocity | REAL | Points per hour |
| comment_velocity | REAL | Comments per hour |
| front_page_presence | INTEGER | Number of endpoints |
| recency | REAL | Decay-weighted recency score |
| composite | REAL | Weighted composite score (0-100) |
| scored_at | TIMESTAMP | When scored |

### summaries

| Column | Type | Description |
|---|---|---|
| id | INTEGER PK | Auto-increment |
| story_id | INTEGER | FK to stories |
| summary_text | TEXT | LLM-generated summary |
| source_text_hash | TEXT | SHA-256 of the article text used to generate this summary |
| status | TEXT | "pending_validation", "validated", "rejected", "no_summary" |
| generated_at | TIMESTAMP | When generated |

### validations

| Column | Type | Description |
|---|---|---|
| id | INTEGER PK | Auto-increment |
| summary_id | INTEGER | FK to summaries |
| result | TEXT | "pass", "fail" |
| details | JSON | Per-claim citation check results |
| validated_at | TIMESTAMP | When validated |

### digests

| Column | Type | Description |
|---|---|---|
| id | INTEGER PK | Auto-increment |
| period_start | TIMESTAMP | Start of digest period |
| period_end | TIMESTAMP | End of digest period |
| content_json | JSON | Structured digest data |
| content_md | TEXT | Rendered markdown digest |
| story_count | INTEGER | Number of stories included |
| created_at | TIMESTAMP | When assembled |

### orchestrator_decisions

| Column | Type | Description |
|---|---|---|
| id | INTEGER PK | Auto-increment |
| story_id | INTEGER | FK to stories |
| decision | TEXT | "dispatched", "skipped", "budget_exceeded" |
| reason | TEXT | Human-readable reason for the decision |
| priority_score | REAL | Composite score at time of decision |
| budget_remaining | INTEGER | Estimated tokens remaining when decision was made |
| used_llm | INTEGER | 1 if LLM was consulted for this decision, 0 otherwise |
| decided_at | TIMESTAMP | When the decision was made |

### chat_sessions

| Column | Type | Description |
|---|---|---|
| id | TEXT PK | UUID session identifier |
| started_at | TIMESTAMP | When the session began |
| last_active | TIMESTAMP | Last message timestamp |
| message_count | INTEGER | Number of messages in session |

### chat_messages

| Column | Type | Description |
|---|---|---|
| id | INTEGER PK | Auto-increment |
| session_id | TEXT | FK to chat_sessions |
| role | TEXT | "user" or "assistant" |
| content | TEXT | Message text |
| tool_calls | JSON | Tool invocations and results (nullable) |
| created_at | TIMESTAMP | When the message was created |

---

## 7. System Startup and Lifecycle

### Startup Sequence

1. Supervisor initializes message bus (creates all channels)
2. Supervisor initializes SQLite database (run migrations if needed)
3. Supervisor starts all agents as async tasks:
   - collector
   - orchestrator
   - fetcher
   - categorizer
   - scorer
   - summarizer
   - validator
   - report-builder
   - chat
4. Supervisor starts FastAPI server (separate async task)
5. Supervisor enters health monitoring loop

Collector begins first poll immediately. Orchestrator, categorizer, and scorer begin processing as stories arrive on the story channel. Orchestrator dispatches fetch requests to the fetcher based on priority and budget. Summarizer waits for summarize_request messages from the orchestrator. Validator waits for summaries. Report builder waits for its scheduled trigger or a manual request. Chat agent waits for chat_request messages.

### Modes

- **Continuous:** Agents run indefinitely. Collector polls every 10 minutes. Report builder triggers on schedule (default every 6 hours).
- **Backfill:** Collector fetches stories from HN /v0/topstories and /v0/beststories, then pipeline processes the backlog.
- **On-demand digest:** CLI or API triggers report builder immediately for the current period.

### State Management

Each agent persists results to SQLite after processing each message. If the system crashes, agents resume from the database. Stories already collected are not re-fetched (deduplication by HN item ID as primary key). Articles already fetched are not re-fetched (check articles table by story_id). Summaries already validated are not re-validated.

### Error Handling

- Collector failure (HN API down): Log error, retry next poll cycle. Other agents unaffected.
- Fetcher failure (individual URL): Mark story as fetch_failed. Move to next story. Other fetches unaffected.
- Categorizer failure: Log error. Story still has score and can appear in digest as "uncategorized."
- Scorer failure: Log error. Story still has category and can appear in digest unranked.
- Summarizer failure (LLM error): Mark as no_summary. Story appears in digest without summary.
- Validator failure: Summary stays as pending_validation. Story appears in digest without summary.
- Report builder failure: Log error. Data persists. Next scheduled run or manual trigger rebuilds.
- Orchestrator failure: Log error. Stories still flow to categorizer and scorer. Fetcher and summarizer receive no new requests until orchestrator recovers. Previously dispatched work continues.
- Chat agent failure: Log error. Chat requests return an error response. All other pipeline agents unaffected.
- Agent crash: Supervisor restarts with exponential backoff. Max 3 retries, then alert operator.

No failure in any single agent prevents the digest from being generated. A digest with missing summaries or categories is still useful.

---

## 8. MCP Servers

| Server | Tools |
|---|---|
| hn-mcp | fetch_top_stories(), fetch_new_stories(), fetch_best_stories(), fetch_show_stories(), fetch_ask_stories(), fetch_job_stories(), fetch_item(id) |
| web-mcp | fetch_url(url, timeout), extract_article_text(html) |
| sqlite-mcp | read_query(sql), write_event(table, data) |
| llm-mcp | generate_summary(article_text, title), validate_summary(summary, source_text) |
| analytics-mcp | query_stories(), query_digests(), get_story_detail(), category_breakdown(), trending_categories(), top_stories(), score_distribution(), author_activity(), keyword_trend(), daily_volume(), compare_days(), system_status() |

---

## 9. Interaction Layer

### CLI

Entry point: `python -m hndigest`

| Command | Description |
|---|---|
| `hndigest start` | Start supervisor + all agents + API server |
| `hndigest stop` | Graceful shutdown |
| `hndigest status` | All agent statuses, last heartbeat, message counts |
| `hndigest digest --now` | Trigger immediate digest generation for current period |
| `hndigest digest --latest` | Display most recent digest |
| `hndigest digest --date 2026-03-27` | Display digest for a specific date |
| `hndigest stories --today` | List all stories collected today with scores |
| `hndigest stories --id 12345` | Full detail for a single story: metadata, article, summary, validation |
| `hndigest categories` | Show category breakdown for today |
| `hndigest agents` | Show registered agents, health, throughput |
| `hndigest config` | Show current configuration (poll interval, scoring weights, category rules) |
| `hndigest chat "query"` | Send a query to the chat agent and display the response |

### FastAPI Server

Runs as an async task alongside agents in the same process.

| Endpoint | Method | Description |
|---|---|---|
| /api/health | GET | System health: agent statuses, uptime |
| /api/digests | GET | List recent digests. Params: limit, since |
| /api/digests/latest | GET | Most recent digest |
| /api/digests/{id} | GET | Specific digest |
| /api/stories | GET | Query stories. Params: category, min_score, since, limit |
| /api/stories/{id} | GET | Full story detail: metadata, article text, summary, validation, score breakdown |
| /api/categories | GET | Category breakdown for current period |
| /api/agents | GET | Agent registry: name, status, last heartbeat, messages processed |
| /api/config | GET | Current configuration |
| /api/digest/generate | POST | Trigger on-demand digest |
| /api/chat | POST | Send a message to the chat agent. Body: {session_id, message}. Response: {session_id, response, tool_calls} |
| /api/events | WebSocket | Live stream of new stories and digest completions |

### Web Dashboard

Single-page application served by FastAPI. Connects to WebSocket for live updates and REST endpoints for data.

**Views:**

- **Daily Digest:** The main view. Today's digest grouped by category, ranked by score. Each entry shows title, summary, score, comment count, links to source and HN discussion. Toggle between current period and historical digests.
- **Story Detail:** Full data chain for a single story: HN metadata, fetched article text, generated summary, validation result, score breakdown, category assignment with method.
- **Live Feed:** Real-time stream of stories as they are collected and processed. Shows pipeline status per story (collected, fetched, categorized, scored, summarized, validated).
- **Agents:** Live agent status, heartbeats, message throughput.
- **Categories:** Visual breakdown of story distribution across categories. Trends over time.
- **Chat:** Conversational interface for querying data. Send natural language questions, receive answers with tool call transparency. Session persistence for multi-turn conversations.

---

## 10. Implementation Phases

### Phase 1: Foundation
- [x] Project structure, SQLite schema, message bus, supervisor lifecycle
- [x] Agent base class with run loop, heartbeat, subscription/publication
- [x] HN collector agent + MCP server (top stories only)
- [x] Scorer agent with velocity calculations
- [x] CLI: start, stop, status, stories
- [x] End-to-end test: collector publishes story -> scorer ranks it -> query via CLI

### Phase 2: Content Pipeline
- [x] Fetcher agent + web MCP server (article text extraction)
- [x] Categorizer agent + YAML category config
- [x] Report builder agent (structured digest from scores + categories, no summaries yet)
- [x] CLI: digest --now, digest --latest, categories
- [x] End-to-end test: story -> fetch -> categorize -> score -> digest without summaries

### Phase 3: Orchestrator
- [ ] Orchestrator agent with priority thresholds and token budget
- [ ] New message bus channels: fetch_request, summarize_request
- [ ] orchestrator_decisions table migration
- [ ] Rewire story channel: orchestrator receives stories, dispatches to fetcher
- [ ] Config: orchestrator.yaml with thresholds, budget, LLM mode flag
- [ ] Decision logging and budget tracking
- [ ] End-to-end test: collector -> orchestrator -> fetcher with priority filtering

### Phase 4: LLM Integration
- [ ] Summarizer agent receives summarize_request from orchestrator
- [ ] Validator agent with retry logic (ADR-003)
- [ ] Summary and validation integrated into digest output
- [ ] Orchestrator optional LLM mode for ambiguous prioritization
- [ ] End-to-end test: full pipeline with validated summaries in digest

### Phase 5: Chat Agent
- [ ] Chat agent with ReAct loop
- [ ] Analytics MCP server with 12 query tools
- [ ] chat_sessions and chat_messages tables
- [ ] CLI: hndigest chat "query"
- [ ] End-to-end test: chat query -> tool calls -> response

### Phase 6: Interface
- [ ] FastAPI server with all REST endpoints including /api/chat
- [ ] WebSocket for live updates
- [ ] Web dashboard with chat view
- [ ] Historical digest browsing
- [ ] Docker containerization
- [ ] Documentation

---

## 11. Testing Strategy

All tests are end-to-end. No mocking of any component at any level.

**Deterministic pipeline tests (no network, no LLM):** Seed SQLite directly with known story, article, and score data. Start the full agent pipeline. Verify that categorizer, scorer, and report builder produce expected output given known input. These tests exercise real agents, real message bus, real SQLite, real supervisor lifecycle.

**Network-dependent tests (flagged `external: true`):** Start full system against the live HN API. Verify collector ingests real stories, fetcher retrieves real articles, categorizer and scorer process them. These tests require network access and skip cleanly when unavailable.

**LLM-dependent tests (flagged `external: true`, requires API key):** Start full pipeline including summarizer and validator against real LLM API. Verify summaries are generated, validation produces pass/fail, and digest includes validated summaries. These tests require an LLM API key and skip cleanly when unavailable.

**Full end-to-end test:** Start entire system. Collector pulls from live HN API. Fetcher retrieves real articles. Categorizer, scorer run. Summarizer and validator call real LLM. Report builder assembles digest. Verify complete digest output with real data. Requires both network and LLM API key.

**Infrastructure tests:** Start supervisor, verify all agents boot, emit heartbeats, and respond to shutdown command. Start FastAPI server, verify all endpoints return correct responses against known database state. Verify WebSocket broadcasts on digest completion.

**Orchestrator tests:** Seed stories with known composite scores. Start orchestrator with a low threshold. Verify stories above threshold produce fetch_request messages. Verify stories below threshold are logged as "skipped" in orchestrator_decisions. Verify budget tracking decrements correctly and stops dispatching when exhausted.

**Chat agent tests:** Send a chat_request with a natural language query. Verify the chat agent calls appropriate analytics-mcp tools. Verify the response is coherent and references real data. Verify conversation persistence in chat_sessions and chat_messages tables. These tests require an LLM API key.

**Analytics MCP tests:** Seed known data. Call each of the 12 analytics tools directly. Verify correct query results against the seeded data. These are deterministic tests that require no network or LLM.

Every external dependency has an env var gate. Missing key or no network = test must not be skipped. Ask for the required configuration before running tests.
