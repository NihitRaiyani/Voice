from dataclasses import FrozenInstanceError
from datetime import UTC, datetime
from decimal import Decimal
from typing import get_type_hints
from uuid import UUID, uuid4

import pytest
from roma.domain.persistence import (
    AuditLogRecord,
    CallCostRecord,
    CallerRecord,
    CallEventRecord,
    FollowupJobRecord,
    PersistenceConflict,
    PersistenceError,
    PersistenceUnavailable,
    ProviderUsageRecord,
    RecordingRecord,
    RecordNotFound,
    SafetyEventRecord,
    hash_phone_e164,
)
from roma.repositories.interfaces.durable import (
    AppointmentRepository,
    CallerRepository,
    CallRepository,
    DurableUnitOfWork,
    EvidenceRepository,
    ReferenceDataRepository,
)

PHONE = "+919876543210"
PEPPER = "p" * 32


def test_hash_phone_e164_is_a_stable_lowercase_sha256_digest():
    digest = hash_phone_e164(PHONE, PEPPER)

    assert digest == hash_phone_e164(PHONE, PEPPER)
    assert len(digest) == 64
    assert digest == digest.lower()
    assert PHONE not in digest


def test_hash_phone_e164_changes_when_pepper_changes():
    assert hash_phone_e164(PHONE, PEPPER) != hash_phone_e164(PHONE, "q" * 32)


def test_hash_phone_e164_changes_for_different_phones_with_the_same_pepper():
    assert hash_phone_e164(PHONE, PEPPER) != hash_phone_e164("+919876543211", PEPPER)


def test_hash_phone_e164_matches_a_known_hmac_sha256_vector():
    assert (
        hash_phone_e164("+14155552671", "0123456789abcdef0123456789abcdef")
        == "843788aace1f32e92c3679f2db8026380a7f4a9c7eec09582cd9af000ef2748b"
    )


@pytest.mark.parametrize(
    "phone",
    [
        "919876543210",
        "+91 9876543210",
        "+91-9876543210",
        "+abc",
        "+١٢٣",
        "+0",
        "+0123456789",
        "+1234567890123456",
    ],
)
def test_hash_phone_e164_rejects_malformed_phone(phone):
    with pytest.raises(ValueError, match=r"E\.164"):
        hash_phone_e164(phone, PEPPER)


def test_hash_phone_e164_rejects_a_short_pepper():
    with pytest.raises(ValueError, match="pepper"):
        hash_phone_e164(PHONE, "too-short")


@pytest.mark.parametrize("phone", ["+1", "+123456789012345"])
def test_hash_phone_e164_accepts_e164_digit_boundaries(phone):
    assert len(hash_phone_e164(phone, PEPPER)) == 64


def test_persistence_records_are_frozen_and_slotted():
    record = CallerRecord(
        id=uuid4(),
        phone_hash=hash_phone_e164(PHONE, PEPPER),
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )

    with pytest.raises(FrozenInstanceError):
        record.phone_hash = "changed"
    with pytest.raises(TypeError):
        vars(record)


def test_persistence_errors_have_a_common_runtime_error_base():
    assert issubclass(PersistenceError, RuntimeError)
    for error in (PersistenceUnavailable, PersistenceConflict, RecordNotFound):
        assert issubclass(error, PersistenceError)


def test_durable_repository_protocols_are_importable():
    for protocol in (
        CallerRepository,
        CallRepository,
        AppointmentRepository,
        EvidenceRepository,
        ReferenceDataRepository,
        DurableUnitOfWork,
    ):
        assert getattr(protocol, "_is_runtime_protocol", False)


def test_reference_data_join_upserts_return_their_composite_identity():
    branch_course_return = get_type_hints(
        ReferenceDataRepository.upsert_branch_course_offering
    )["return"]
    user_role_return = get_type_hints(ReferenceDataRepository.upsert_user_role)["return"]

    assert branch_course_return == tuple[UUID, UUID]
    assert user_role_return == tuple[UUID, UUID]


def test_mapping_fields_are_deeply_immutable_snapshots():
    now = datetime.now(UTC)
    source = {"nested": {"items": [1, {"state": "original"}]}}
    records = (
        (CallEventRecord(uuid4(), uuid4(), "answered", now, payload=source), "payload"),
        (SafetyEventRecord(uuid4(), now, metadata=source), "metadata"),
        (
            ProviderUsageRecord(
                uuid4(), "provider", "llm", Decimal("1"), "token", now, "usage-1",
                provider_metadata=source,
            ),
            "provider_metadata",
        ),
        (
            CallCostRecord(
                uuid4(), "provider", "llm", Decimal("1"), "token", Decimal("0.1"),
                Decimal("0.1"), "INR", "v1", now, "cost-1", metadata=source,
            ),
            "metadata",
        ),
        (
            RecordingRecord(
                uuid4(), uuid4(), "s3", "recording-key", "audio/wav", 1, 1, "checksum",
                "active", now, now, deletion_metadata=source,
            ),
            "deletion_metadata",
        ),
        (
            FollowupJobRecord(
                uuid4(), "followup", source, "pending", 0, now, now, now, "job-1"
            ),
            "payload",
        ),
        (AuditLogRecord(uuid4(), "read", "caller", uuid4(), now, metadata=source), "metadata"),
    )

    source["nested"]["items"].append("later")
    source["nested"]["items"][1]["state"] = "changed"

    for record, field_name in records:
        snapshot = getattr(record, field_name)
        assert snapshot["nested"]["items"] == (1, {"state": "original"})
        with pytest.raises(TypeError):
            snapshot["new"] = "value"
        with pytest.raises(TypeError):
            snapshot["nested"]["new"] = "value"
