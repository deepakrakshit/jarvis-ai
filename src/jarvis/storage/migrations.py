"""Schema Migrations and Table DDL for JARVIS SQLite Storage.

Preserves additive schema changes, strict foreign key constraints,
and WAL mode concurrency.
"""

from typing import List

MIGRATIONS: List[str] = [
    # Initial Schema Migration (v1)
    """
    -- Sessions Table
    CREATE TABLE IF NOT EXISTS sessions (
        session_id TEXT PRIMARY KEY,
        title TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        metadata_json TEXT DEFAULT '{}'
    );

    -- Tasks Table
    CREATE TABLE IF NOT EXISTS tasks (
        task_id TEXT PRIMARY KEY,
        session_id TEXT NOT NULL,
        parent_task_id TEXT,
        task_type TEXT NOT NULL,
        priority TEXT NOT NULL,
        state TEXT NOT NULL,
        raw_intent TEXT NOT NULL,
        normalized_goal TEXT,
        input_payload_json TEXT DEFAULT '{}',
        assigned_model TEXT,
        assigned_agent TEXT,
        token_budget INTEGER DEFAULT 8000,
        retry_count INTEGER DEFAULT 0,
        max_retries INTEGER DEFAULT 3,
        state_history_json TEXT DEFAULT '[]',
        result_summary TEXT,
        verification_passed INTEGER,
        error_message TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        completed_at TEXT,
        FOREIGN KEY(session_id) REFERENCES sessions(session_id) ON DELETE CASCADE
    );

    -- Action Requests Table
    CREATE TABLE IF NOT EXISTS action_requests (
        action_id TEXT PRIMARY KEY,
        task_id TEXT NOT NULL,
        session_id TEXT NOT NULL,
        correlation_id TEXT NOT NULL,
        actor TEXT NOT NULL,
        capability TEXT NOT NULL,
        arguments_json TEXT DEFAULT '{}',
        target TEXT NOT NULL,
        risk_tier TEXT NOT NULL,
        provenance TEXT NOT NULL,
        approval_required INTEGER DEFAULT 0,
        approval_id TEXT,
        timestamp TEXT NOT NULL,
        FOREIGN KEY(task_id) REFERENCES tasks(task_id) ON DELETE CASCADE
    );

    -- Action Results Table
    CREATE TABLE IF NOT EXISTS action_results (
        action_id TEXT PRIMARY KEY,
        task_id TEXT NOT NULL,
        status TEXT NOT NULL,
        execution_target TEXT NOT NULL,
        output_json TEXT,
        error TEXT,
        exit_code INTEGER,
        observation_json TEXT DEFAULT '{}',
        verified INTEGER DEFAULT 0,
        duration_ms REAL DEFAULT 0.0,
        timestamp TEXT NOT NULL,
        audit_logged INTEGER DEFAULT 0,
        FOREIGN KEY(action_id) REFERENCES action_requests(action_id) ON DELETE CASCADE
    );

    -- Approvals Table
    CREATE TABLE IF NOT EXISTS approvals (
        approval_id TEXT PRIMARY KEY,
        action_id TEXT NOT NULL,
        task_id TEXT NOT NULL,
        session_id TEXT NOT NULL,
        capability TEXT NOT NULL,
        target_resource TEXT NOT NULL,
        risk_summary TEXT NOT NULL,
        arguments_summary_json TEXT DEFAULT '{}',
        status TEXT NOT NULL,
        requested_at TEXT NOT NULL,
        decided_at TEXT,
        decided_by TEXT,
        rejection_reason TEXT
    );

    -- Memory Records Table
    CREATE TABLE IF NOT EXISTS memory_records (
        record_id TEXT PRIMARY KEY,
        memory_type TEXT NOT NULL,
        key TEXT NOT NULL,
        content TEXT NOT NULL,
        metadata_json TEXT DEFAULT '{}',
        provenance_source TEXT NOT NULL,
        trust_level TEXT NOT NULL,
        importance_score REAL DEFAULT 0.5,
        relevance_tags_json TEXT DEFAULT '[]',
        session_id TEXT,
        task_id TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        accessed_at TEXT NOT NULL,
        access_count INTEGER DEFAULT 0
    );

    -- Model Quotas Table
    CREATE TABLE IF NOT EXISTS model_quotas (
        model_family TEXT PRIMARY KEY,
        provider TEXT NOT NULL,
        max_rpm INTEGER DEFAULT 30,
        max_tpm INTEGER DEFAULT 8000,
        max_rpd INTEGER DEFAULT 1000,
        current_rpm_used INTEGER DEFAULT 0,
        current_tpm_used INTEGER DEFAULT 0,
        current_rpd_used INTEGER DEFAULT 0,
        remaining_requests INTEGER DEFAULT 1000,
        remaining_tokens INTEGER DEFAULT 8000,
        reset_epoch_seconds REAL DEFAULT 0.0,
        current_concurrency INTEGER DEFAULT 0,
        max_concurrency INTEGER DEFAULT 5,
        provider_healthy INTEGER DEFAULT 1,
        recent_latency_ms REAL DEFAULT 0.0,
        failure_rate_percent REAL DEFAULT 0.0,
        last_checked_at TEXT NOT NULL
    );

    -- Audit Log Table (Append-Only)
    CREATE TABLE IF NOT EXISTS audit_log (
        log_id INTEGER PRIMARY KEY AUTOINCREMENT,
        timestamp TEXT NOT NULL,
        event_type TEXT NOT NULL,
        action_id TEXT,
        task_id TEXT,
        session_id TEXT,
        capability TEXT,
        verdict TEXT,
        details_json TEXT DEFAULT '{}'
    );

    -- Indexes for fast query retrieval
    CREATE INDEX IF NOT EXISTS idx_tasks_session ON tasks(session_id);
    CREATE INDEX IF NOT EXISTS idx_tasks_state ON tasks(state);
    CREATE INDEX IF NOT EXISTS idx_actions_task ON action_requests(task_id);
    CREATE INDEX IF NOT EXISTS idx_memory_key ON memory_records(key);
    CREATE INDEX IF NOT EXISTS idx_memory_type ON memory_records(memory_type);
    CREATE INDEX IF NOT EXISTS idx_audit_action ON audit_log(action_id);
    """,
    # Standing Intents and Memory Full-Text Search (v2)
    """
    -- Standing Intents Table
    CREATE TABLE IF NOT EXISTS standing_intents (
        id TEXT PRIMARY KEY,
        description TEXT NOT NULL,
        trigger_keywords_json TEXT NOT NULL,
        scope TEXT NOT NULL DEFAULT 'conversation',
        status TEXT NOT NULL DEFAULT 'armed',
        expires_at TEXT,
        max_fires INTEGER DEFAULT 3,
        fire_count INTEGER DEFAULT 0,
        cooldown_seconds INTEGER DEFAULT 86400,
        last_fired_at TEXT,
        created_at TEXT NOT NULL,
        session_id TEXT,
        metadata_json TEXT DEFAULT '{}'
    );

    CREATE INDEX IF NOT EXISTS idx_standing_intents_status ON standing_intents(status);
    CREATE INDEX IF NOT EXISTS idx_standing_intents_session ON standing_intents(session_id);

    -- Memory Full-Text Search Table
    CREATE VIRTUAL TABLE IF NOT EXISTS memory_fts USING fts5(
        record_id UNINDEXED,
        content,
        key,
        relevance_tags
    );

    -- Synchronize triggers for FTS index
    CREATE TRIGGER IF NOT EXISTS trg_memory_records_ai AFTER INSERT ON memory_records BEGIN
        INSERT INTO memory_fts(record_id, content, key, relevance_tags)
        VALUES (new.record_id, new.content, new.key, new.relevance_tags_json);
    END;

    CREATE TRIGGER IF NOT EXISTS trg_memory_records_ad AFTER DELETE ON memory_records BEGIN
        DELETE FROM memory_fts WHERE record_id = old.record_id;
    END;

    CREATE TRIGGER IF NOT EXISTS trg_memory_records_au AFTER UPDATE ON memory_records BEGIN
        DELETE FROM memory_fts WHERE record_id = old.record_id;
        INSERT INTO memory_fts(record_id, content, key, relevance_tags)
        VALUES (new.record_id, new.content, new.key, new.relevance_tags_json);
    END;

    -- Backfill existing memory_records into memory_fts
    INSERT INTO memory_fts(record_id, content, key, relevance_tags)
    SELECT record_id, content, key, relevance_tags_json FROM memory_records;
    """,
    # Scheduled Jobs & Heartbeat Run Logs (v3)
    """
    -- Scheduled Jobs Table
    CREATE TABLE IF NOT EXISTS scheduled_jobs (
        job_id TEXT PRIMARY KEY,
        name TEXT NOT NULL,
        interval_seconds INTEGER NOT NULL,
        raw_intent TEXT NOT NULL,
        enabled INTEGER DEFAULT 1,
        last_run_at TEXT,
        next_run_at TEXT,
        created_at TEXT NOT NULL,
        metadata_json TEXT DEFAULT '{}'
    );

    CREATE INDEX IF NOT EXISTS idx_scheduled_jobs_enabled ON scheduled_jobs(enabled);

    -- Heartbeat Run Audit Table
    CREATE TABLE IF NOT EXISTS heartbeat_runs (
        run_id INTEGER PRIMARY KEY AUTOINCREMENT,
        timestamp TEXT NOT NULL,
        status TEXT NOT NULL,
        standing_intents_checked INTEGER DEFAULT 0,
        standing_intents_fired INTEGER DEFAULT 0,
        jobs_checked INTEGER DEFAULT 0,
        jobs_triggered INTEGER DEFAULT 0,
        summary TEXT
    );
    """,
    # ACP Sessions & Event Ledger (v4)
    """
    -- ACP Sessions Table
    CREATE TABLE IF NOT EXISTS acp_sessions (
        session_id TEXT PRIMARY KEY,
        parent_task_id TEXT,
        harness_type TEXT NOT NULL,
        model TEXT NOT NULL,
        repo_path TEXT NOT NULL,
        branch TEXT,
        state TEXT NOT NULL,
        allowed_tools_json TEXT NOT NULL,
        network_allowed INTEGER DEFAULT 0,
        read_only INTEGER DEFAULT 0,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        metadata_json TEXT DEFAULT '{}'
    );

    CREATE INDEX IF NOT EXISTS idx_acp_sessions_state ON acp_sessions(state);
    CREATE INDEX IF NOT EXISTS idx_acp_sessions_parent ON acp_sessions(parent_task_id);

    -- ACP Event Ledger Table
    CREATE TABLE IF NOT EXISTS acp_events (
        event_id TEXT PRIMARY KEY,
        session_id TEXT NOT NULL,
        event_type TEXT NOT NULL,
        payload_json TEXT NOT NULL,
        timestamp TEXT NOT NULL,
        FOREIGN KEY(session_id) REFERENCES acp_sessions(session_id) ON DELETE CASCADE
    );

    CREATE INDEX IF NOT EXISTS idx_acp_events_session ON acp_events(session_id);
    """,
    # Artifacts Storage (v5)
    """
    -- Artifacts Table
    CREATE TABLE IF NOT EXISTS artifacts (
        artifact_id TEXT PRIMARY KEY,
        session_id TEXT,
        task_id TEXT,
        agent_id TEXT,
        artifact_type TEXT NOT NULL,
        title TEXT NOT NULL,
        mime_type TEXT NOT NULL,
        size_bytes INTEGER DEFAULT 0,
        checksum TEXT NOT NULL,
        storage_path TEXT NOT NULL,
        sensitivity TEXT DEFAULT 'internal',
        retention TEXT DEFAULT 'durable',
        created_at TEXT NOT NULL,
        metadata_json TEXT DEFAULT '{}'
    );

    CREATE INDEX IF NOT EXISTS idx_artifacts_session ON artifacts(session_id);
    CREATE INDEX IF NOT EXISTS idx_artifacts_task ON artifacts(task_id);
    CREATE INDEX IF NOT EXISTS idx_artifacts_type ON artifacts(artifact_type);
    """,
]
