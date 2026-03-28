-- 002_orchestrator_decisions.sql
-- Tracks orchestrator dispatch decisions for every story evaluated.

CREATE TABLE orchestrator_decisions (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    story_id         INTEGER NOT NULL REFERENCES stories(id),
    decision         TEXT    NOT NULL,  -- "dispatched", "skipped", "budget_exceeded"
    reason           TEXT    NOT NULL,  -- Human-readable reason for the decision
    priority_score   REAL    NOT NULL,  -- Composite score at time of decision
    budget_remaining INTEGER NOT NULL,  -- Estimated tokens remaining when decision was made
    used_llm         INTEGER NOT NULL DEFAULT 0,  -- 1 if LLM was consulted, 0 otherwise
    decided_at       TEXT    NOT NULL   -- ISO 8601 UTC
);

CREATE INDEX idx_orchestrator_decisions_story_id ON orchestrator_decisions (story_id);
CREATE INDEX idx_orchestrator_decisions_decision ON orchestrator_decisions (decision);
CREATE INDEX idx_orchestrator_decisions_decided_at ON orchestrator_decisions (decided_at);
