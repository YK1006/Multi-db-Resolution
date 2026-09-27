from app.db import get_connection


def find_by_identifier(id_type: str, normalized_value: str) -> list[int]:
    with get_connection() as connection:
        rows = connection.execute(
            """SELECT DISTINCT raw_record_id FROM identifiers
               WHERE identifier_type = %s AND normalized_value = %s ORDER BY raw_record_id""",
            (id_type, normalized_value),
        ).fetchall()
    return [row["raw_record_id"] for row in rows]