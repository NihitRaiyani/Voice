"""Database-independent checks for the PostgreSQL schema contract."""

from dataclasses import fields
from typing import get_args, get_type_hints

import pytest
from roma.domain import persistence
from roma.repositories.postgres.models import Base
from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKeyConstraint,
    Numeric,
    UniqueConstraint,
)
from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import CreateIndex, CreateTable

TABLES = {
    "callers",
    "institutes",
    "branches",
    "courses",
    "branch_courses",
    "counsellors",
    "calls",
    "call_turns",
    "call_events",
    "appointments",
    "appointment_slots",
    "safety_events",
    "provider_usage",
    "call_costs",
    "recordings",
    "followup_jobs",
    "users",
    "roles",
    "user_roles",
    "audit_logs",
}


def test_complete_schema_has_named_primary_keys_and_postgres_uuid_defaults():
    assert set(Base.metadata.tables) == TABLES
    for name, table in Base.metadata.tables.items():
        expected = (
            {"branch_id", "course_id"}
            if name == "branch_courses"
            else ({"user_id", "role_id"} if name == "user_roles" else {"id"})
        )
        assert set(table.primary_key.columns.keys()) == expected
        assert table.primary_key.name == f"pk_{name}"
        for column in table.primary_key.columns:
            assert isinstance(column.type, postgresql.UUID)
            assert column.type.as_uuid
            assert not column.nullable
        if "id" in table.c:
            assert str(table.c.id.server_default.arg) == "gen_random_uuid()"


@pytest.mark.parametrize(
    ("table", "columns", "target", "ondelete"),
    [
        ("branches", "institute_id", "institutes.id", "RESTRICT"),
        ("courses", "institute_id", "institutes.id", "RESTRICT"),
        ("branch_courses", "branch_id", "branches.id", "RESTRICT"),
        ("branch_courses", "course_id", "courses.id", "RESTRICT"),
        ("user_roles", "user_id", "users.id", "CASCADE"),
        ("user_roles", "role_id", "roles.id", "CASCADE"),
        ("counsellors", "branch_id", "branches.id", "RESTRICT"),
        ("counsellors", "user_id", "users.id", "RESTRICT"),
        ("calls", "caller_id", "callers.id", "SET NULL"),
        ("call_turns", "call_id", "calls.id", "CASCADE"),
        ("call_events", "call_id", "calls.id", "CASCADE"),
        ("appointment_slots", "branch_id", "branches.id", "RESTRICT"),
        ("appointments", "caller_id", "callers.id", "SET NULL"),
        ("appointments", "call_id", "calls.id", "SET NULL"),
        ("appointments", "counsellor_id", "counsellors.id", "SET NULL"),
        ("appointments", "course_id", "courses.id", "SET NULL"),
        (
            "appointments",
            "branch_id,appointment_date,start_time",
            "appointment_slots.branch_id,appointment_slots.appointment_date,appointment_slots.start_time",
            "RESTRICT",
        ),
        ("safety_events", "call_id", "calls.id", "SET NULL"),
        ("safety_events", "turn_id", "call_turns.id", "SET NULL"),
        ("provider_usage", "call_id", "calls.id", "SET NULL"),
        ("provider_usage", "turn_id", "call_turns.id", "SET NULL"),
        ("call_costs", "call_id", "calls.id", "SET NULL"),
        ("call_costs", "provider_usage_id", "provider_usage.id", "SET NULL"),
        ("recordings", "call_id", "calls.id", "CASCADE"),
        ("followup_jobs", "call_id", "calls.id", "SET NULL"),
        ("followup_jobs", "appointment_id", "appointments.id", "SET NULL"),
        ("audit_logs", "actor_user_id", "users.id", "SET NULL"),
    ],
)
def test_foreign_keys_preserve_history_and_limit_cascades(table, columns, target, ondelete):
    source = Base.metadata.tables[table]
    matches = [
        constraint
        for constraint in source.constraints
        if isinstance(constraint, ForeignKeyConstraint)
        and list(constraint.columns.keys()) == columns.split(",")
    ]
    assert len(matches) == 1
    constraint = matches[0]
    assert constraint.name
    assert [element.target_fullname for element in constraint.elements] == target.split(",")
    assert constraint.ondelete == ondelete
    if ondelete == "SET NULL":
        assert all(column.nullable for column in constraint.columns)


@pytest.mark.parametrize(
    ("table", "columns"),
    [
        ("institutes", "code"),
        ("branches", "institute_id,code"),
        ("courses", "institute_id,code"),
        ("roles", "name"),
        ("users", "email"),
        ("counsellors", "user_id"),
        ("counsellors", "branch_id,employee_code"),
        ("callers", "phone_hash"),
        ("calls", "provider_call_id"),
        ("call_turns", "call_id,turn_number"),
        ("call_events", "idempotency_key"),
        ("appointment_slots", "branch_id,appointment_date,start_time"),
        ("provider_usage", "idempotency_key"),
        ("call_costs", "idempotency_key"),
        ("recordings", "storage_provider,object_key"),
        ("followup_jobs", "idempotency_key"),
    ],
)
def test_business_keys_have_named_unique_constraints(table, columns):
    assert any(
        isinstance(constraint, UniqueConstraint)
        and list(constraint.columns.keys()) == columns.split(",")
        and constraint.name == f"uq_{table}_{columns.replace(',', '_')}"
        for constraint in Base.metadata.tables[table].constraints
    )


@pytest.mark.parametrize(
    ("table", "check", "expression"),
    [
        ("users", "email_lowercase", "email = lower(email)"),
        ("calls", "direction", "direction IN ('inbound', 'outbound')"),
        (
            "calls",
            "status",
            "status IN ('pending', 'ringing', 'in_progress', 'completed', 'failed', 'cancelled', 'no_answer', 'busy')",
        ),
        ("calls", "total_cost_nonnegative", "total_cost >= 0"),
        ("calls", "currency_length", "char_length(currency) = 3"),
        ("call_turns", "turn_number_positive", "turn_number > 0"),
        ("call_turns", "speaker", "speaker IN ('caller', 'agent', 'system')"),
        ("call_turns", "latency_ms_nonnegative", "latency_ms >= 0"),
        ("appointment_slots", "capacity_positive", "capacity > 0"),
        ("appointment_slots", "time_order", "end_time > start_time"),
        (
            "appointment_slots",
            "status",
            "status IN ('available', 'held', 'booked', 'blocked', 'cancelled')",
        ),
        (
            "appointments",
            "status",
            "status IN ('booked', 'confirmed', 'completed', 'cancelled', 'no_show')",
        ),
        ("provider_usage", "service", "service IN ('telephony', 'stt', 'llm', 'tts')"),
        ("provider_usage", "measured_units_nonnegative", "measured_units >= 0"),
        ("call_costs", "quantity_nonnegative", "quantity >= 0"),
        ("call_costs", "unit_price_nonnegative", "unit_price >= 0"),
        ("call_costs", "amount_nonnegative", "amount >= 0"),
        ("call_costs", "currency_length", "char_length(currency) = 3"),
        ("recordings", "duration_ms_nonnegative", "duration_ms >= 0"),
        ("recordings", "size_bytes_nonnegative", "size_bytes >= 0"),
        ("recordings", "status", "status IN ('pending', 'available', 'deleted', 'failed')"),
        ("followup_jobs", "attempts_nonnegative", "attempts >= 0"),
        (
            "followup_jobs",
            "status",
            "status IN ('pending', 'running', 'succeeded', 'failed', 'dead_letter')",
        ),
    ],
)
def test_named_checks_enforce_business_invariants(table, check, expression):
    checks = {
        constraint.name: str(constraint.sqltext)
        for constraint in Base.metadata.tables[table].constraints
        if isinstance(constraint, CheckConstraint)
    }
    assert checks[f"ck_{table}_{check}"] == expression


@pytest.mark.parametrize(
    ("table", "columns"),
    [
        ("branches", "institute_id,is_active"),
        ("courses", "institute_id,is_active"),
        ("counsellors", "branch_id,is_active"),
        ("calls", "caller_id,created_at"),
        ("calls", "status,created_at"),
        ("calls", "started_at"),
        ("call_turns", "call_id,created_at"),
        ("call_events", "call_id,occurred_at"),
        ("appointment_slots", "branch_id,appointment_date,status,start_time"),
        ("appointments", "caller_id,appointment_date"),
        ("appointments", "branch_id,appointment_date,status"),
        ("safety_events", "call_id,created_at"),
        ("safety_events", "rule,created_at"),
        ("provider_usage", "call_id,service,occurred_at"),
        ("provider_usage", "provider,service,occurred_at"),
        ("call_costs", "call_id,occurred_at"),
        ("call_costs", "provider,service,occurred_at"),
        ("recordings", "retention_until,status"),
        ("followup_jobs", "status,available_at"),
        ("audit_logs", "actor_user_id,occurred_at"),
        ("audit_logs", "resource_type,resource_id,occurred_at"),
    ],
)
def test_query_indexes_are_named_and_ordered(table, columns):
    assert any(
        list(index.columns.keys()) == columns.split(",") and index.name
        for index in Base.metadata.tables[table].indexes
    )


def test_active_appointment_unique_index_is_postgres_partial():
    indexes = {index.name: index for index in Base.metadata.tables["appointments"].indexes}
    active = indexes["uq_appointments_active_slot"]
    assert active.unique
    assert list(active.columns.keys()) == ["branch_id", "appointment_date", "start_time"]
    assert (
        str(active.dialect_options["postgresql"]["where"])
        == "status IN ('booked', 'confirmed')"
    )
    ddl = str(CreateIndex(active).compile(dialect=postgresql.dialect()))
    assert "CREATE UNIQUE INDEX" in ddl
    assert "WHERE status IN ('booked', 'confirmed')" in ddl


@pytest.mark.parametrize(
    ("table", "record"),
    [
        ("callers", persistence.CallerRecord),
        ("calls", persistence.CallRecord),
        ("call_turns", persistence.CallTurnRecord),
        ("call_events", persistence.CallEventRecord),
        ("appointment_slots", persistence.AppointmentSlotRecord),
        ("appointments", persistence.AppointmentRecord),
        ("safety_events", persistence.SafetyEventRecord),
        ("provider_usage", persistence.ProviderUsageRecord),
        ("call_costs", persistence.CallCostRecord),
        ("recordings", persistence.RecordingRecord),
        ("followup_jobs", persistence.FollowupJobRecord),
        ("audit_logs", persistence.AuditLogRecord),
    ],
)
def test_storage_column_names_match_domain_records(table, record):
    assert set(Base.metadata.tables[table].c.keys()) == {field.name for field in fields(record)}
    for name, annotation in get_type_hints(record).items():
        assert Base.metadata.tables[table].c[name].nullable == (
            type(None) in get_args(annotation)
        )


def test_postgres_types_nullability_defaults_and_append_only_timestamps():
    metadata = Base.metadata
    for table in metadata.tables.values():
        for column in table.columns:
            if isinstance(column.type, DateTime):
                assert column.type.timezone, f"{table.name}.{column.name}"
            if column.name in {"payload", "metadata", "provider_metadata", "deletion_metadata"}:
                assert isinstance(column.type, postgresql.JSONB)
                assert not column.nullable
                assert str(column.server_default.arg) == "'{}'::jsonb"
            if column.name in {"created_at", "updated_at", "occurred_at"}:
                assert not column.nullable
                assert str(column.server_default.arg) == "now()"
        assert all(constraint.name for constraint in table.constraints)
        assert "CREATE TABLE" in str(CreateTable(table).compile(dialect=postgresql.dialect()))
    assert metadata.tables["users"].c.email.type.__class__.__name__ == "String"
    assert metadata.tables["callers"].c.phone_hash.type.length == 64
    assert not {"phone", "phone_number"} & set(metadata.tables["callers"].c.keys())
    for table, column in [
        ("calls", "total_cost"),
        ("provider_usage", "measured_units"),
        ("call_costs", "quantity"),
        ("call_costs", "unit_price"),
        ("call_costs", "amount"),
    ]:
        assert isinstance(metadata.tables[table].c[column].type, Numeric)
    assert (
        metadata.tables["calls"].c.total_cost.type.precision,
        metadata.tables["calls"].c.total_cost.type.scale,
    ) == (14, 4)
    for name in ["provider_usage", "call_costs", "followup_jobs"]:
        assert not metadata.tables[name].c.idempotency_key.nullable
    for table, column in [
        ("calls", "provider_call_id"),
        ("call_events", "idempotency_key"),
        ("counsellors", "user_id"),
    ]:
        assert metadata.tables[table].c[column].nullable
    for name in [
        "call_turns",
        "call_events",
        "safety_events",
        "provider_usage",
        "call_costs",
        "recordings",
        "audit_logs",
    ]:
        assert "updated_at" not in metadata.tables[name].c
    assert not metadata.tables["recordings"].c.retention_until.nullable
    assert "recording_url" not in metadata.tables["recordings"].c
