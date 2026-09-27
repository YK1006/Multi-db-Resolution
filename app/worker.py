import asyncio
import logging
import threading
import traceback
from pathlib import Path

from app.db import get_connection
from app.ingest.csv_ingest import ingest_csv
from app.ingest.inspect import inspect_source
from app.ingest.sql_ingest import ingest_sql_dump
from app.normalize.populate_identifiers import populate_identifiers_for_batch
from app.matching.enrich import process_new_batch
from app.stats import recompute_stats

logger = logging.getLogger(__name__)
_active_batches: set[int] = set()
_active_lock = threading.Lock()


def _claim(batch_id: int) -> bool:
    with _active_lock:
        if batch_id in _active_batches:
            return False
        _active_batches.add(batch_id)
        return True


def process_batch_pipeline(batch_id: int) -> None:
    if not _claim(batch_id):
        return
    try:
        with get_connection() as connection:
            batch = connection.execute(
                """SELECT b.*, s.type AS source_type FROM import_batches b
                   JOIN sources s ON s.id = b.source_id WHERE b.id = %s""",
                (batch_id,),
            ).fetchone()
            if not batch or batch["processing_complete"]:
                return
            if not batch["file_path"] or not Path(batch["file_path"]).exists():
                raise FileNotFoundError("The uploaded source file is unavailable for processing or recovery.")
            connection.execute(
                "UPDATE import_batches SET started_at = coalesce(started_at, now()), status = 'cleaning' WHERE id = %s",
                (batch_id,),
            )

        source = inspect_source(batch["file_path"])
        if batch["source_type"] == "csv":
            table = source["tables"][0]
            with get_connection() as connection:
                connection.execute("UPDATE import_batches SET total_rows = %s WHERE id = %s", (_count_csv_rows(batch["file_path"]), batch_id))
            ingest_csv(batch["file_path"], batch["source_id"], table["table_name"], batch_id)
        else:
            ingest_sql_dump(batch["file_path"], batch["source_id"], batch_id)
            with get_connection() as connection:
                count = connection.execute("SELECT count(*) FROM raw_records WHERE batch_id = %s", (batch_id,)).fetchone()
                connection.execute("UPDATE import_batches SET total_rows = %s WHERE id = %s", (count["count"], batch_id))

        _set_status(batch_id, "indexing", 0)
        populate_identifiers_for_batch(batch_id)
        _set_status(batch_id, "matching", 90)
        _set_status(batch_id, "enriching", 95)
        process_new_batch(batch_id)
        with get_connection() as connection:
            connection.execute(
                """UPDATE import_batches SET status = 'completed', progress = 100, completed_at = now(),
                   processing_complete = true WHERE id = %s""",
                (batch_id,),
            )
        recompute_stats()
    except Exception as error:
        logger.exception("Batch %s failed", batch_id)
        try:
            with get_connection() as connection:
                connection.execute(
                    """UPDATE import_batches SET status = 'completed_with_errors', completed_at = now(),
                       processing_complete = true, error_log = concat_ws(E'\\n', error_log, %s) WHERE id = %s""",
                    (f"{error}\n{traceback.format_exc()}", batch_id),
                )
            recompute_stats()
        except Exception:
            logger.exception("Unable to persist failure state for batch %s", batch_id)
    finally:
        with _active_lock:
            _active_batches.discard(batch_id)


def _set_status(batch_id: int, status: str, progress: int) -> None:
    with get_connection() as connection:
        connection.execute("UPDATE import_batches SET status = %s, progress = %s WHERE id = %s", (status, progress, batch_id))


def _count_csv_rows(file_path: str) -> int:
    import pandas as pd

    total = 0
    for frame in pd.read_csv(file_path, chunksize=5000, dtype=str, keep_default_na=False):
        total += len(frame)
    return total


async def resume_interrupted_batches() -> None:
    while True:
        try:
            with get_connection() as connection:
                batches = connection.execute(
                    """SELECT id FROM import_batches WHERE processing_complete = false
                       AND status IN ('cleaning', 'indexing', 'matching', 'enriching') ORDER BY id"""
                ).fetchall()
            for batch in batches:
                await asyncio.to_thread(process_batch_pipeline, batch["id"])
        except Exception:
            logger.exception("Batch recovery scan failed")
        await asyncio.sleep(15)