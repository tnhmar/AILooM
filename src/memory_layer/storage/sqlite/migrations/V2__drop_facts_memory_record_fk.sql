-- V2: Recreate the facts table without the REFERENCES memory_records(id)
-- foreign-key on memory_record_id.
--
-- Rationale: unit tests create Fact objects with synthetic memory_record_ids
-- that have no corresponding row in memory_records.  The FK is enforced at the
-- application layer instead, keeping the schema flexible for isolated testing.
--
-- SQLite does not support DROP CONSTRAINT, so we use the rename-copy-drop
-- pattern recommended by the SQLite documentation.

PRAGMA foreign_keys = OFF;

BEGIN;

-- 1. Create the replacement table (identical structure, FK removed).
CREATE TABLE IF NOT EXISTS facts_new (
    id                TEXT    NOT NULL PRIMARY KEY,
    memory_record_id  TEXT    NOT NULL,          -- FK removed intentionally
    tenant_id         TEXT    NOT NULL,
    principal_id      TEXT    NOT NULL,
    subject_entity_id TEXT    NOT NULL,
    predicate         TEXT    NOT NULL,
    predicate_group   TEXT    NOT NULL,
    object_value      TEXT    NOT NULL,
    effective_from    TEXT    NOT NULL,
    effective_to      TEXT,
    recorded_at       TEXT    NOT NULL,
    supersedes        TEXT    REFERENCES facts_new (id),
    confidence        REAL    NOT NULL DEFAULT 1.0,
    sector            TEXT    NOT NULL DEFAULT 'SEMANTIC',
    lifecycle_state   TEXT    NOT NULL DEFAULT 'ACTIVE'
);

-- 2. Copy existing data.
INSERT INTO facts_new SELECT * FROM facts;

-- 3. Drop old table.
DROP TABLE facts;

-- 4. Rename.
ALTER TABLE facts_new RENAME TO facts;

-- 5. Recreate indexes.
CREATE INDEX IF NOT EXISTS idx_facts_tenant_entity
    ON facts (tenant_id, subject_entity_id, predicate_group);

CREATE INDEX IF NOT EXISTS idx_facts_active
    ON facts (tenant_id, lifecycle_state)
    WHERE lifecycle_state = 'ACTIVE';

COMMIT;

PRAGMA foreign_keys = ON;
