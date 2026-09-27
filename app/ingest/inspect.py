import csv
import re
from pathlib import Path

import pandas as pd
import sqlparse


INSERT_RE = re.compile(
    r"INSERT\s+INTO\s+[\"`]?([\w.]+)[\"`]?\s*\((.*?)\)\s*VALUES\s*((?:\s*\(.*\)\s*,?)+)",
    re.IGNORECASE | re.DOTALL,
)


def _sample_values(value_clause: str) -> list[str]:
    tuples = _split_value_tuples(value_clause)
    if not tuples:
        return []
    inner = tuples[0]
    values, start, quote, depth, index = [], 0, False, 0, 0
    while index < len(inner):
        char = inner[index]
        if char == "'":
            if quote and index + 1 < len(inner) and inner[index + 1] == "'":
                index += 1
            else:
                quote = not quote
        elif char == "(" and not quote:
            depth += 1
        elif char == ")" and not quote:
            depth -= 1
        elif char == "," and not quote and depth == 0:
            values.append(inner[start:index].strip())
            start = index + 1
        index += 1
    values.append(inner[start:].strip())
    return [value[1:-1].replace("''", "'") if value.startswith("'") and value.endswith("'") else value for value in values]


def _split_value_tuples(value_clause: str) -> list[str]:
    tuples, start, depth, quote, index = [], None, 0, False, 0
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
                tuples.append(value_clause[start:index])
                start = None
        index += 1
    return tuples


def inspect_source(file_path: str | Path) -> dict:
    path = Path(file_path)
    if path.suffix.lower() == ".csv":
        frame = pd.read_csv(path, nrows=5, dtype=str, keep_default_na=False)
        return {
            "type": "csv",
            "tables": [{"table_name": path.stem, "columns": list(frame.columns), "sample_rows": frame.to_dict("records")}],
        }

    tables: dict[str, dict] = {}
    with path.open("r", encoding="utf-8-sig", errors="replace") as stream:
        for statement in sqlparse.split(stream.read()):
            match = INSERT_RE.search(statement.strip().rstrip(";"))
            if not match:
                continue
            table = match.group(1).split(".")[-1].strip('"`')
            columns = [column.strip().strip('"`') for column in match.group(2).split(",")]
            entry = tables.setdefault(table, {"table_name": table, "columns": columns, "sample_rows": []})
            if not entry["sample_rows"]:
                values = _sample_values(match.group(3))
                if len(values) == len(columns):
                    entry["sample_rows"].append(dict(zip(columns, values)))
    if not tables:
        raise ValueError("No supported INSERT INTO statements were found in this SQL dump.")
    return {"type": "sql", "tables": list(tables.values())}