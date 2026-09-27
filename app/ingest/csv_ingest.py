from pathlib import Path

import pandas as pd
from psycopg.types.json import Jsonb

from app.db import get_connection


def ingest_csv(file_path: str | Path, source_id: int, table_name: str, batch_id: int) -> int:
    path = Path(file_path)
    with get_connection() as connection:
        batch = connection.execute(
            "SELECT last_processed_row, total_rows FROM import_batches WHERE id = %s", (batch_id,)
        ).fetchone()
    checkpoint = int(batch["last_processed_row"] or 0) if batch else 0
    total_rows = int(batch["total_rows"] or 0) if batch else 0
    if not total_rows:
        total_rows = _count_rows(path)
    total = checkpoint
    errors: list[str] = []
    with get_connection() as connection:
        connection.execute("UPDATE import_batches SET total_rows = %s WHERE id = %s", (total_rows, batch_id))
        chunks = pd.read_csv(
            path,
            chunksize=5000,
            dtype=str,
            keep_default_na=False,
            skiprows=range(1, checkpoint + 1) if checkpoint else None,
        )
        for frame in chunks:
            rows = []
            for offset, record in enumerate(frame.to_dict("records")):
                row_number = checkpoint + offset + 1
                try:
                    rows.append((source_id, table_name, f"{batch_id}:{row_number}", batch_id, Jsonb(record)))
                except (TypeError, ValueError) as error:
                    errors.append(f"row {row_number}: {error}")
            if rows:
                try:
                    with connection.transaction():
                        with connection.cursor() as cursor:
                            cursor.executemany(
                                """INSERT INTO raw_records(source_id, table_name, row_id, batch_id, original_data)
                                   VALUES (%s, %s, %s, %s, %s)
                                   ON CONFLICT (source_id, table_name, row_id) DO NOTHING""",
                                rows,
                            )
                except Exception:
                    for row in rows:
                        try:
                            with connection.transaction():
                                connection.execute(
                                    """INSERT INTO raw_records(source_id, table_name, row_id, batch_id, original_data)
                                       VALUES (%s, %s, %s, %s, %s)
                                       ON CONFLICT (source_id, table_name, row_id) DO NOTHING""",
                                    row,
                                )
                        except Exception as error:
                            errors.append(f"row {row[2]}: {error}")
            total += len(frame)
            checkpoint = total
            with connection.cursor() as cursor:
                cursor.execute(
                    "UPDATE import_batches SET progress = %s, last_processed_row = %s WHERE id = %s",
                    (min(100, int(total * 100 / max(1, total_rows))), total, batch_id),
                )
            connection.commit()
        if errors:
            connection.execute(
                "UPDATE import_batches SET error_log = concat_ws(E'\\n', error_log, %s) WHERE id = %s",
                ("\n".join(errors), batch_id),
            )
    return total


def _count_rows(path: Path) -> int:
    total = 0
    for frame in pd.read_csv(path, chunksize=5000, dtype=str, keep_default_na=False):
        total += len(frame)
    return total