from app.db import get_connection, jsonb
from app.normalize.clean import normalize_record

IDENTIFIER_FIELDS = {"email", "phone", "username", "member_id"}


def populate_identifiers_for_batch(batch_id: int) -> int:
    with get_connection() as connection:
        records = connection.execute(
            """SELECT r.id, r.source_id, r.table_name, r.original_data
               FROM raw_records r WHERE r.batch_id = %s AND r.processed_at IS NULL ORDER BY r.id""",
            (batch_id,),
        ).fetchall()
        mappings = connection.execute(
            """SELECT table_name, original_column, canonical_field FROM field_mappings
               WHERE source_id = (SELECT source_id FROM import_batches WHERE id = %s) AND confirmed = true""",
            (batch_id,),
        ).fetchall()
        by_table: dict[str, dict[str, str]] = {}
        for row in mappings:
            by_table.setdefault(row["table_name"], {})[row["original_column"]] = row["canonical_field"]

        indexed = 0
        for record in records:
            normalized = normalize_record(record["original_data"] or {}, by_table.get(record["table_name"], {}))
            connection.execute("DELETE FROM identifiers WHERE raw_record_id = %s", (record["id"],))
            for field in IDENTIFIER_FIELDS:
                value = normalized.get(field)
                if value:
                    raw_value = next(
                        (str(record["original_data"].get(column)) for column, canonical in by_table.get(record["table_name"], {}).items() if canonical == field and record["original_data"].get(column)),
                        str(value),
                    )
                    connection.execute(
                        "INSERT INTO identifiers(raw_record_id, identifier_type, raw_value, normalized_value) VALUES (%s, %s, %s, %s)",
                        (record["id"], field, raw_value, value.lower() if field in {"username", "member_id"} else value),
                    )
            connection.execute(
                "UPDATE raw_records SET normalized_data = %s, processed_at = now() WHERE id = %s",
                (jsonb(normalized), record["id"]),
            )
            indexed += 1
        connection.execute("UPDATE import_batches SET progress = 100, last_processed_row = total_rows WHERE id = %s", (batch_id,))
    return indexed