# Architecture

## Ingestion
CSV files are streamed with pandas in 5,000-row chunks and persisted as JSONB raw records. SQL INSERT dumps are split into statements and parsed by table, preserving the source table and column structure. A unique source/table/row key makes retries idempotent, and every upload gets its own import batch and optional part label.

## Large-file processing, batching, and resumability
The worker stores status, progress, total rows, last processed row, and an error log on each batch. CSV ingestion resumes from its durable row checkpoint, while indexing only selects records that have not yet been normalized; matching skips records already linked to an entity. A lightweight startup polling task resumes active processing stages, and completed batches are terminal so re-uploading a source does not replay older batches.

## Field mapping
Normalized column aliases are mapped by a synonym table with fuzzy matching for close variants. Unresolved columns may be sent to an optional LLM endpoint using `AI_API_KEY`; any network, response, or JSON failure falls back to deterministic rules. Suggestions are persisted as unconfirmed mappings and the user reviews each field and sample before ingestion begins.

## Normalization
Email addresses are trimmed, lowercased, and checked for a basic email shape. Phone numbers are reduced to digits, with an India country prefix removed, and values shorter than seven digits are discarded. Names, usernames, member IDs, companies, and addresses receive whitespace cleanup and common null-token handling; the original JSONB payload is retained alongside normalized values.

## Matching hierarchy
Identifiers are indexed by type and normalized value in PostgreSQL. The default ordered hierarchy is email, phone, username, and member ID, and the list can be extended in `app/matching/config.py`. The search and batch resolver use the same indexed identifier model so matching behavior is consistent.

## Enrichment algorithm
For each seed identifier, enrichment uses a breadth-first queue and a seen set to prevent repeated searches. Every matched record contributes its other known identifiers to the queue, allowing a chain such as email to phone to username to discover further records, bounded by the configured hop limit. Each successful hop records the searched identifier, the sources found, and newly discovered identifiers.

## Master repository and traceability
Resolved records are linked through `entity_records` and their cleaned fields are stored in `entity_fields`. Each selected field records both its raw record and source name, and entity detail shows the source table and original payload. Existing field values are retained when a conflicting value arrives; the conflict is appended to the batch error log rather than silently replacing provenance.

## Background processing
The upload route inspects a file and saves suggested mappings, then waits for a human confirmation before scheduling processing. FastAPI background tasks execute each batch, advancing through cleaning, indexing, matching, and enrichment, with terminal success or error status saved to PostgreSQL. A small async polling loop on startup resumes non-terminal processing batches without requiring a separate queue service.

## Caching and scaling
There is no Redis dependency: PostgreSQL indexes serve identifier lookups, and mapping suggestions are inexpensive enough to recompute per uploaded table. At higher traffic, Redis could cache repeated search results and deterministic mapping suggestions, with invalidation tied to new batches and confirmed mappings. Large CSVs are streamed in chunks to bound memory, though database writes and entity resolution will eventually benefit from COPY-based bulk loading and worker concurrency controls.

## Error handling
Invalid file types are rejected before inspection, unsupported SQL dumps produce a clear inspection error, and optional AI mapping failures degrade to deterministic suggestions. Batch exceptions are captured with a traceback in the batch log and reflected as `completed_with_errors`; row-level insertion issues are logged where possible. Original data is retained for audit and later reprocessing, while unconfirmed or ignored fields do not enter the normalized entity profile.

## Future database additions
Adding a source requires uploading its CSV or supported SQL INSERT dump, reviewing detected columns, and confirming the suggested canonical fields. New aliases can be added to the synonym map without changing the schema, and new identifier types can be added to normalization and the hierarchy. The `part_label` field allows multiple batches to be associated with a single named source while preserving each upload's processing history.