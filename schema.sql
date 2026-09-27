CREATE TABLE IF NOT EXISTS sources (
    id SERIAL PRIMARY KEY,
    name TEXT,
    type TEXT CHECK (type IN ('csv', 'sql')),
    uploaded_by TEXT,
    created_at TIMESTAMP DEFAULT now()
);

CREATE TABLE IF NOT EXISTS import_batches (
    id SERIAL PRIMARY KEY,
    source_id INT REFERENCES sources(id),
    part_label TEXT,
    status TEXT DEFAULT 'uploaded' CHECK (status IN ('uploaded', 'inspecting', 'mapping', 'cleaning', 'indexing', 'matching', 'enriching', 'completed', 'completed_with_errors')),
    progress INT DEFAULT 0,
    total_rows INT,
    last_processed_row INT DEFAULT 0,
    error_log TEXT,
    started_at TIMESTAMP,
    completed_at TIMESTAMP,
    created_at TIMESTAMP DEFAULT now(),
    processing_complete BOOLEAN DEFAULT false,
    file_path TEXT
);

CREATE TABLE IF NOT EXISTS raw_records (
    id SERIAL PRIMARY KEY,
    source_id INT REFERENCES sources(id),
    table_name TEXT,
    row_id TEXT,
    batch_id INT REFERENCES import_batches(id),
    original_data JSONB,
    normalized_data JSONB,
    processed_at TIMESTAMP,
    UNIQUE(source_id, table_name, row_id)
);

CREATE TABLE IF NOT EXISTS identifiers (
    id SERIAL PRIMARY KEY,
    raw_record_id INT REFERENCES raw_records(id),
    identifier_type TEXT,
    raw_value TEXT,
    normalized_value TEXT
);
CREATE INDEX IF NOT EXISTS idx_identifiers_lookup ON identifiers(identifier_type, normalized_value);
CREATE INDEX IF NOT EXISTS idx_raw_records_batch ON raw_records(batch_id);

CREATE TABLE IF NOT EXISTS entities (
    id SERIAL PRIMARY KEY,
    is_new BOOLEAN DEFAULT true,
    created_at TIMESTAMP DEFAULT now(),
    updated_at TIMESTAMP DEFAULT now()
);

CREATE TABLE IF NOT EXISTS entity_records (
    entity_id INT REFERENCES entities(id),
    raw_record_id INT REFERENCES raw_records(id),
    PRIMARY KEY(entity_id, raw_record_id),
    UNIQUE(raw_record_id)
);

CREATE TABLE IF NOT EXISTS entity_fields (
    id SERIAL PRIMARY KEY,
    entity_id INT REFERENCES entities(id),
    field_name TEXT,
    field_value TEXT,
    source_raw_record_id INT REFERENCES raw_records(id),
    source_name TEXT,
    updated_at TIMESTAMP DEFAULT now(),
    UNIQUE(entity_id, field_name)
);

CREATE TABLE IF NOT EXISTS entity_enrichment_trail (
    id SERIAL PRIMARY KEY,
    entity_id INT REFERENCES entities(id),
    hop_number INT,
    identifier_type TEXT,
    identifier_value TEXT,
    found_in_source TEXT,
    newly_discovered JSONB
);

CREATE TABLE IF NOT EXISTS field_mappings (
    id SERIAL PRIMARY KEY,
    source_id INT REFERENCES sources(id),
    table_name TEXT,
    original_column TEXT,
    canonical_field TEXT,
    confirmed BOOLEAN DEFAULT false,
    UNIQUE(source_id, table_name, original_column)
);

CREATE TABLE IF NOT EXISTS stats (
    id SERIAL PRIMARY KEY,
    metric_name TEXT UNIQUE,
    metric_value BIGINT DEFAULT 0,
    updated_at TIMESTAMP DEFAULT now()
);