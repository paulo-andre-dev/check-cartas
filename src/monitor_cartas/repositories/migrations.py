"""DDL idempotente. Cada execução do main.py roda isso; CREATE TABLE IF NOT EXISTS."""

SCHEMA = """
CREATE TABLE IF NOT EXISTS quotas (
    source_site TEXT NOT NULL,
    source_id TEXT NOT NULL,
    fingerprint TEXT,
    source_url TEXT NOT NULL,
    status TEXT NOT NULL,
    status_raw TEXT,
    modality TEXT,
    administrator TEXT,
    administrator_cnpj TEXT,
    "group" TEXT,
    quota TEXT,
    data_json TEXT NOT NULL,
    first_seen_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    missing_runs INTEGER NOT NULL DEFAULT 0,
    silenced INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (source_site, source_id)
);

CREATE INDEX IF NOT EXISTS idx_quotas_fingerprint ON quotas(fingerprint);
CREATE INDEX IF NOT EXISTS idx_quotas_administrator ON quotas(administrator);
CREATE INDEX IF NOT EXISTS idx_quotas_status ON quotas(status);

CREATE TABLE IF NOT EXISTS quota_observations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_site TEXT NOT NULL,
    source_id TEXT NOT NULL,
    observed_at TEXT NOT NULL,
    data_json TEXT NOT NULL,
    FOREIGN KEY (source_site, source_id) REFERENCES quotas(source_site, source_id)
);

CREATE TABLE IF NOT EXISTS quota_price_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_site TEXT NOT NULL,
    source_id TEXT NOT NULL,
    changed_at TEXT NOT NULL,
    field TEXT NOT NULL,
    old_value TEXT,
    new_value TEXT,
    FOREIGN KEY (source_site, source_id) REFERENCES quotas(source_site, source_id)
);

CREATE TABLE IF NOT EXISTS quota_status_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_site TEXT NOT NULL,
    source_id TEXT NOT NULL,
    changed_at TEXT NOT NULL,
    old_status TEXT,
    new_status TEXT NOT NULL,
    FOREIGN KEY (source_site, source_id) REFERENCES quotas(source_site, source_id)
);

CREATE TABLE IF NOT EXISTS administrators (
    administrator TEXT PRIMARY KEY,
    cnpj TEXT,
    data_json TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS administrator_rules (
    administrator TEXT PRIMARY KEY,
    data_json TEXT NOT NULL,
    validation_status TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS combinations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    computed_at TEXT NOT NULL,
    administrator TEXT NOT NULL,
    rule_status TEXT NOT NULL,
    data_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS alerts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    sent_at TEXT NOT NULL,
    kind TEXT NOT NULL,
    source_site TEXT,
    source_id TEXT,
    chat_id TEXT,
    message TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS silenced_quotas (
    source_site TEXT NOT NULL,
    source_id TEXT NOT NULL,
    silenced_at TEXT NOT NULL,
    reactivated_at TEXT,
    reason TEXT,
    PRIMARY KEY (source_site, source_id)
);

CREATE TABLE IF NOT EXISTS scraper_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    trigger TEXT NOT NULL,
    success INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS adapter_results (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER NOT NULL,
    site TEXT NOT NULL,
    started_at TEXT NOT NULL,
    finished_at TEXT NOT NULL,
    access_ok INTEGER NOT NULL,
    block_reason TEXT,
    listing_count INTEGER NOT NULL DEFAULT 0,
    processed_count INTEGER NOT NULL DEFAULT 0,
    error_count INTEGER NOT NULL DEFAULT 0,
    errors_json TEXT,
    FOREIGN KEY (run_id) REFERENCES scraper_runs(id)
);

CREATE TABLE IF NOT EXISTS raw_evidence (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_site TEXT NOT NULL,
    source_id TEXT NOT NULL,
    collected_at TEXT NOT NULL,
    adapter_version TEXT,
    http_status INTEGER,
    evidence_path TEXT NOT NULL,
    content_hash TEXT NOT NULL
);
"""


def apply_schema(conn) -> None:
    conn.executescript(SCHEMA)
    conn.commit()
