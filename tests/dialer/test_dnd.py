from roma.dialer.dnd import DoNotCallRegistry, StubRegistry, normalize_phone


def test_consented_number_is_dialable():
    reg = StubRegistry(consented={"+919876543210"})
    assert reg.is_dialable("+919876543210") is True


def test_unknown_number_is_blocked():
    reg = StubRegistry(consented={"+919876543210"})
    assert reg.is_dialable("+919999999999") is False


def test_empty_registry_blocks_everything():
    reg = StubRegistry()
    assert reg.is_dialable("+919876543210") is False


def test_suppression_wins_over_consent():
    reg = StubRegistry(
        consented={"+919876543210"},
        suppressed={"+919876543210"},
    )
    assert reg.is_dialable("+919876543210") is False


def test_phone_normalization_equivalence():
    assert normalize_phone("+91 98765 43210") == normalize_phone("9876543210")
    assert normalize_phone("098765-43210") == normalize_phone("9876543210")

    reg = StubRegistry(consented={"9876543210"})
    assert reg.is_dialable("+91 98765 43210") is True
    assert reg.is_dialable("098765-43210") is True


def test_stub_satisfies_registry_protocol():
    assert isinstance(StubRegistry(), DoNotCallRegistry)
