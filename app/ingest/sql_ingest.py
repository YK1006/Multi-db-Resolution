from pathlib import Path

import sqlparse
from psycopg.types.json import Jsonb

from app.db import get_connection
from app.ingest.inspect import INSERT_RE


def _split_top_level(value: str, separator: str = ",") -> list[str]:
    parts, start, depth, quote = [], 0, 0, None
    index = 0
    while index < len(value):
        char = value[index]
        if quote:
            if char == quote:
                if quote == "'" and index + 1 < len(value) and value[index + 1] == "'":
                    index += 1
                else:
                    quote = None
        elif char in "'\"":
            quote = char
        elif char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
        elif char == separator and depth == 0:
            parts.append(value[start:index].strip())
            start = index + 1
        index += 1
    parts.append(value[start:].strip())
    return parts


def _parse_literal(value: str):
    value = value.strip()
    if value.upper() == "NULL":
        return None
    if len(value) >= 2 and value[0] == value[-1] == "'":
        return value[1:-1].replace("''", "'")
    if value.upper() in {"TRUE", "FALSE"}:
        return value.upper() == "TRUE"
    try:
        return int(value)
    except ValueError:
        try:
            return float(value)
        except ValueError:
            return value


def _value_tuples(value_clause: str):
    depth, quote, start, index = 0, False, None, 0
    while index < len(value_clause):
        char = value_clause[index]
        if char == "'":
            if quote and index + 1 < len(value_clause) and value_clause[index + 1] == "'":
                index += 1
            else:
                quote = not quote
        elif not quote and char == "(":
            if depth == 0:
                start = index + 1
            depth += 1
        elif not quote and char == ")":
            depth -= 1
            if depth == 0 and start is not None:
                yield [_parse_literal(item) for item in _split_top_level(value_clause[start:index])]
                start = None
        index += 1


def ingest_sql_dump(file_path: str | Path, source_id: int, batch_id: int | None = None) -> int:
    path = Path(file_path)
    total = 0
    with path.open("r", encoding="utf-8-sig", errors="replace") as stream:
        statements = sqlparse.split(stream.read())
    errors = []
    with get_connection() as connection:
        for statement in statements:
            match = INSERT_RE.search(statement.strip().rstrip(";"))
            if not match:
                continue
            table_name = match.group(1).split(".")[-1].strip('"`')
            columns = [column.strip().strip('"`') for column in match.group(2).split(",")]
            for values in _value_tuples(match.group(3)):
                row_number = total + 1
                try:
                    record = dict(zip(columns, values))
                    row_id = f"{batch_id or 0}:{table_name}:{row_number}"
                    connection.execute(
                        """INSERT INTO raw_records(source_id, table_name, row_id, batch_id, original_data)
                           VALUES (%s, %s, %s, %s, %s) ON CONFLICT (source_id, table_name, row_id) DO NOTHING""",
                        (source_id, table_name, row_id, batch_id, Jsonb(record)),
                    )
                    total += 1
                except Exception as error:
                    errors.append(f"{table_name} row {row_number}: {error}")
        if batch_id is not None:
            connection.execute(
                "UPDATE import_batches SET progress = 100, last_processed_row = %s WHERE id = %s",
                (total, batch_id),
            )
            if errors:
                connection.execute(
                    "UPDATE import_batches SET error_log = concat_ws(E'\\n', error_log, %s) WHERE id = %s",
                    ("\n".join(errors), batch_id),
                )
    return total