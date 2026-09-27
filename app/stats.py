from app.db import get_connection


def recompute_stats() -> None:
    queries = {
        "total_databases": "SELECT count(*) FROM sources",
        "total_tables": "SELECT count(DISTINCT (source_id, table_name)) FROM raw_records",
        "total_records": "SELECT count(*) FROM raw_records",
        "records_processed": "SELECT count(*) FROM raw_records WHERE processed_at IS NOT NULL",
        "records_matched": "SELECT count(*) FROM entity_records er WHERE (SELECT count(*) FROM entity_records x WHERE x.entity_id = er.entity_id) > 1",
        "new_entities_created": "SELECT count(*) FROM entities WHERE is_new = true",
        "entities_enriched": "SELECT count(DISTINCT entity_id) FROM entity_enrichment_trail",
        "duplicate_records_detected": "SELECT greatest(count(*) - count(DISTINCT (source_id, table_name, row_id)), 0) FROM raw_records",
        "unresolved_records": "SELECT count(*) FROM raw_records r LEFT JOIN entity_records er ON er.raw_record_id = r.id WHERE er.raw_record_id IS NULL",
        "processing_failures": "SELECT count(*) FROM import_batches WHERE status = 'completed_with_errors'",
        "total_processing_time_seconds": "SELECT coalesce(sum(extract(epoch FROM (completed_at - started_at))), 0)::bigint FROM import_batches WHERE completed_at IS NOT NULL AND started_at IS NOT NULL",
        "last_processed_time": "SELECT coalesce(extract(epoch FROM max(completed_at)), 0)::bigint FROM import_batches",
    }
    with get_connection() as connection:
        for metric, query in queries.items():
            row = connection.execute(query).fetchone()
            value = next(iter(row.values())) if row else 0
            connection.execute(
                """INSERT INTO stats(metric_name, metric_value, updated_at) VALUES (%s, %s, now())
                   ON CONFLICT (metric_name) DO UPDATE SET metric_value = EXCLUDED.metric_value, updated_at = now()""",
                (metric, int(value or 0)),
            )