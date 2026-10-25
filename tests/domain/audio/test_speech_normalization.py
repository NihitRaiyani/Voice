import pytest
from roma.services.tts_service import normalize_tts_text


@pytest.mark.parametrize(
    "language,time,day,year,count",
    [
        ("en", "ten thirty in the morning", "five November", "twenty twenty six", "twenty three"),
        ("hi", "सुबह साढ़े दस बजे", "पाँच नवंबर", "बीस छब्बीस", "तेईस"),
        ("gu", "સવારે સાડા દસ વાગ્યે", "પાંચ નવેમ્બર", "વીસ છવ્વીસ", "તેવીસ"),
    ],
)
def test_booking_dates_times_and_cardinals_are_natural(language, time, day, year, count):
    text = normalize_tts_text("2026-11-05 10:30 AM slot 23", language)
    assert time in text and day in text and year in text and count in text
    assert not any(c.isdecimal() for c in text)
    assert normalize_tts_text(text, language) == text


@pytest.mark.parametrize("language", ["hi", "gu", "en"])
def test_explicit_indian_date_convention_matches_iso(language):
    assert normalize_tts_text("10/11/2026", language) == normalize_tts_text("2026-11-10", language)


@pytest.mark.parametrize("language", ["en", "hi", "gu"])
@pytest.mark.parametrize("text", [
    "2026-02-30", "2026-2-05", "05-11-2026", "11/13/2026", "1/2/26",
    "24:30", "10:65 AM", "00:30 AM", "13:30 PM", "10:30:45", "10:3 AM",
    "10:30 A.M.", "10:30 XM", "12.50", "1,234", "-23", "10000", "00023",
    "9876543210", "+91 98765 43210", "123 456 7890", "૯૮૭૬૫૪૩૨૧૦", "fee23",
])
def test_undeclared_or_invalid_numeric_forms_fail_closed(language, text):
    with pytest.raises(ValueError) as error:
        normalize_tts_text(text, language)
    assert text not in str(error.value)


@pytest.mark.parametrize("language,expected", [
    ("hi", "अपॉइंटमेंट ब्रांच स्लॉट कन्फर्म"),
    ("gu", "અપોઇન્ટમેન્ટ બ્રાન્ચ સ્લોટ કન્ફર્મ"),
    ("en", "appointment branch slot confirm"),
])
def test_only_finite_booking_terms_are_rendered_without_translating_names(language, expected):
    assert normalize_tts_text("appointment branch slot confirm", language) == expected
    names = "Nihit Vadodara appointmentville branchlet slotting confirmation"
    assert normalize_tts_text(names, language) == names
    assert normalize_tts_text(expected, language) == expected


@pytest.mark.parametrize("language,expected", [
    ("en", "nine thousand nine hundred ninety nine"),
    ("hi", "नौ हज़ार नौ सौ निन्यानवे"),
    ("gu", "નવ હજાર નવ સો નવ્વાણું"),
])
def test_year_and_cardinal_upper_bound_is_inclusive(language, expected):
    assert normalize_tts_text("9999", language) == expected


@pytest.mark.parametrize("language", ["hi", "gu", "en"])
def test_clock_edges_and_nfc_are_idempotent(language):
    for source in ("12:00 AM", "12:00 PM", "23:59", "00:00", "01:30 AM", "02:30 PM", "11:45 AM", "10:15 AM"):
        normalized = normalize_tts_text(source, language)
        assert ":" not in normalized and not any(c.isdecimal() for c in normalized)
        assert normalize_tts_text(normalized, language) == normalized


@pytest.mark.parametrize("language,midnight,early_morning", [
    ("en", "twelve o'clock at night", "one thirty at night"),
    ("hi", "रात बारह बजे", "रात डेढ़ बजे"),
    ("gu", "રાત્રે બાર વાગ્યે", "રાત્રે દોઢ વાગ્યે"),
])
def test_midnight_and_early_hours_use_night_period(language, midnight, early_morning):
    assert normalize_tts_text("12:00 AM", language) == midnight
    assert normalize_tts_text("1:30 AM", language) == early_morning


@pytest.mark.parametrize("language,periods", [
    ("en", ["at night", "in the morning", "in the afternoon", "in the evening", "at night"]),
    ("hi", ["रात", "सुबह", "दोपहर", "शाम", "रात"]),
    ("gu", ["રાત્રે", "સવારે", "બપોરે", "સાંજે", "રાત્રે"]),
])
def test_day_period_boundaries(language, periods):
    for source, period in zip(("04:00", "05:00", "12:00", "17:00", "21:00"), periods, strict=True):
        assert period in normalize_tts_text(source, language)
