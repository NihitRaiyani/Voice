"""Small pgvector SQL type using bound textual vectors, without driver codecs."""

import math

from sqlalchemy.dialects.postgresql.base import ischema_names
from sqlalchemy.types import UserDefinedType


def vector_values(value):
    if not isinstance(value, (tuple, list)) or not 1 <= len(value) <= 16000:
        raise ValueError("Invalid embedding vector")
    if any(
        isinstance(item, bool) or not isinstance(item, (int, float)) or not math.isfinite(item)
        for item in value
    ):
        raise ValueError("Invalid embedding vector")
    values = tuple(float(item) for item in value)
    if not any(values):
        raise ValueError("Invalid embedding vector")
    return values


def vector_literal(value):
    return "[" + ",".join(str(item) for item in vector_values(value)) + "]"


class Vector(UserDefinedType):
    cache_ok = True

    def get_col_spec(self, **kw):
        return "VECTOR"

    def bind_processor(self, dialect):
        return lambda value: None if value is None else vector_literal(value)

    def result_processor(self, dialect, coltype):
        def convert(value):
            if value is None:
                return None
            return tuple(float(item) for item in value.strip("[]").split(","))

        return convert

    def compare_against_backend(self, dialect, conn_type):
        return isinstance(conn_type, Vector)


# Teach Alembic's dialect reflection the installed extension's type name.
ischema_names.setdefault("vector", Vector)
