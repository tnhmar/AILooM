-- memory-layer SQLite schema V1
-- All tables include tenant_id for row-level isolation.
-- Applied by migration_runner.py; do NOT edit after deployment.

-- ---------------------------------------------------------------------------
-- Schema version tracking
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS schema_version (
    version     INTEGER PRIMARY KEY,
    description TEXT    NOT NULL,
    applied_at  TEXT    NOT NULL  -- ISO-8601 UTC
);

-- ---------------------------------------------------------------------------
-- Core record store
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS memory_records (
    id              TEXT    NOT NULL PRIMARY KEY,
    tenant_id       TEXT    NOT NULL,
    principal_id    TEXT    NOT NULL,
    principal_type  TEXT    NOT NULL,
    workspace_id    TEXT,
    session_id      TEXT,
    run_id          TEXT,
    raw_payload     TEXT    NOT NULL,
    payload_type    TEXT    NOT NULL,
    sector          TEXT    NOT NULL,
    lifecycle_state TEXT    NOT NULL DEFAULT 'ACTIVE',
    pipeline_status TEXT    NOT NULL DEFAULT 'PENDING',
    recorded_at     TEXT    NOT NULL,  -- ISO-8601 UTC
    idempotency_key TEXT,
    metadata        TEXT    NOT NULL DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS idx_mr_tenant_lifecycle
    ON memory_records (tenant_id, lifecycle_state);

CREATE INDEX IF NOT EXISTS idx_mr_tenant_session
    ON memory_records (tenant_id, session_id);

-- Partial unique index: only enforced when idempotency_key is present.
CREATE UNIQUE INDEX IF NOT EXISTS idx_mr_idempotency
    ON memory_records (tenant_id, idempotency_key)
    WHERE idempotency_key IS NOT NULL;

-- ---------------------------------------------------------------------------
-- Fact store with temporal validity
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS facts (
    id                TEXT    NOT NULL PRIMARY KEY,
    memory_record_id  TEXT    NOT NULL REFERENCES memory_records (id),
    tenant_id         TEXT    NOT NULL,
    principal_id      TEXT    NOT NULL,
    subject_entity_id TEXT    NOT NULL,
    predicate         TEXT    NOT NULL,
    predicate_group   TEXT    NOT NULL,
    object_value      TEXT    NOT NULL,
    effective_from    TEXT    NOT NULL,  -- ISO-8601 UTC
    effective_to      TEXT,              -- NULL = currently valid
    recorded_at       TEXT    NOT NULL,
    supersedes        TEXT    REFERENCES facts (id),
    confidence        REAL    NOT NULL DEFAULT 1.0,
    sector            TEXT    NOT NULL DEFAULT 'SEMANTIC',
    lifecycle_state   TEXT    NOT NULL DEFAULT 'ACTIVE'
);

CREATE INDEX IF NOT EXISTS idx_facts_tenant_entity
    ON facts (tenant_id, subject_entity_id, predicate_group);

CREATE INDEX IF NOT EXISTS idx_facts_active
    ON facts (tenant_id, lifecycle_state)
    WHERE lifecycle_state = 'ACTIVE';

-- ---------------------------------------------------------------------------
-- Append-only audit log
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS audit_log (
    id          TEXT    NOT NULL PRIMARY KEY,
    tenant_id   TEXT    NOT NULL,
    principal_id TEXT   NOT NULL,
    operation   TEXT    NOT NULL,
    memory_id   TEXT,
    actor       TEXT    NOT NULL DEFAULT 'system',
    timestamp   TEXT    NOT NULL,  -- ISO-8601 UTC
    outcome     TEXT    NOT NULL DEFAULT 'SUCCESS',
    detail      TEXT    NOT NULL DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS idx_audit_tenant_memory
    ON audit_log (tenant_id, memory_id);

CREATE INDEX IF NOT EXISTS idx_audit_tenant_timestamp
    ON audit_log (tenant_id, timestamp DESC);

-- ---------------------------------------------------------------------------
-- Tenant policy store
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS tenant_policies (
    tenant_id  TEXT NOT NULL PRIMARY KEY,
    policies   TEXT NOT NULL,       -- JSON blob
    updated_at TEXT NOT NULL        -- ISO-8601 UTC
);
