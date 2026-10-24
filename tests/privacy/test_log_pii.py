import logging

from roma.core.logging import LeadTokenFilter, RedactionFilter


def test_phone_email_and_capabilities_are_masked_in_logs():
    record = logging.LogRecord(
        "test",
        logging.INFO,
        "",
        0,
        "caller %s %s %s",
        (
            "9876543210",
            "person@example.invalid",
            "/answer?intent=private.token.value&lead=private-lead&token=private-playback",
        ),
        None,
    )
    RedactionFilter([]).filter(record)
    LeadTokenFilter().filter(record)
    message = record.getMessage()
    assert "9876543210" not in message and "******3210" in message
    assert "person@example.invalid" not in message
    assert (
        "private.token.value" not in message
        and "private-playback" not in message
        and "private-lead" not in message
    )
