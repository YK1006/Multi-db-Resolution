from app.db import get_connection
from app.mapping.ai_mapper import suggest_mapping_ai
from app.mapping.rules import suggest_mapping_rules


def map_source(source_id: int, tables: list[dict]) -> list[dict]:
    prepared = []
    with get_connection() as connection:
        for table in tables:
            columns = table["columns"]
            suggestions = suggest_mapping_rules(columns)
            unresolved = [column for column in columns if suggestions[column] == "unknown"]
            if unresolved:
                ai_suggestions = suggest_mapping_ai(unresolved, table.get("sample_rows", []))
                suggestions.update(ai_suggestions)
            for column in columns:
                field = suggestions.get(column, "unknown")
                if field == "unknown":
                    field = "ignore"
                connection.execute(
                    """INSERT INTO field_mappings(source_id, table_name, original_column, canonical_field, confirmed)
                       VALUES (%s, %s, %s, %s, false)
                       ON CONFLICT (source_id, table_name, original_column)
                       DO UPDATE SET canonical_field = EXCLUDED.canonical_field, confirmed = false""",
                    (source_id, table["table_name"], column, field),
                )
                prepared.append({"table_name": table["table_name"], "column": column, "suggestion": field})
    return prepared