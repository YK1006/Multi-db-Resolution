from contextlib import contextmanager
from typing import Iterator

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from app.config import require_database_url


@contextmanager
def get_connection() -> Iterator[psycopg.Connection]:
    with psycopg.connect(require_database_url(), row_factory=dict_row) as connection:
        yield connection


def jsonb(value):
    return Jsonb(value)