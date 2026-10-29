"""Strict positive literal requests only; all negations/ambiguity defer to extraction.

Candidate selection does not authorize booking. Existing availability, readback,
explicit consent and SQL-commit checks remain mandatory.
"""

import re
import unicodedata

from roma.domain.appointments.slots import TimeSlot
from roma.domain.safety.inputs import BRANCHES

_HOURS = {
    "एक": 1, "दो": 2, "तीन": 3, "चार": 4, "पांच": 5, "पाँच": 5,
    "छह": 6, "सात": 7, "आठ": 8, "नौ": 9, "दस": 10, "ग्यारह": 11, "बारह": 12,
    "એક": 1, "બે": 2, "ત્રણ": 3, "ચાર": 4, "પાંચ": 5, "છ": 6,
    "સાત": 7, "આઠ": 8, "નવ": 9, "દસ": 10, "અગિયાર": 11, "બાર": 12,
}
_PERIODS = {"सुबह": "morning", "दोपहर": "afternoon", "दो पहर": "afternoon",
            "शाम": "evening", "સવારે": "morning", "બપોરે": "afternoon", "સાંજે": "evening"}
_BRANCHES = {"वडोदरा": "Vadodara", "વડોદરા": "Vadodara",
             "अहमदाबाद": "Ahmedabad", "અમદાવાદ": "Ahmedabad"}
_CLOCK = "|".join(re.escape(word) for word in _HOURS) + "|1[0-2]|[1-9]"
_HINDI = re.compile(
    rf"कल (?P<period>सुबह|दोपहर|दो पहर|शाम) (?P<hour>{_CLOCK}) बजे "
    r"(?:(?P<branch>वडोदरा|अहमदाबाद) (?:ब्रांच|ब्रान्च) में )?"
    r"(?:(?:मुलाकात|अपॉइंटमेंट) बुक (?:कीजिए|कीजिये|कर दीजिए)|रखिए|रखिये)"
)
_GUJARATI = re.compile(
    rf"કાલે (?P<period>સવારે|બપોરે|સાંજે) (?P<hour>{_CLOCK}) વાગ્યે "
    r"(?:(?P<branch>વડોદરા|અમદાવાદ) (?:બ્રાન્ચમાં|બ્રાંચમાં|બ્રાન્ચ માં) )?"
    r"(?:મુલાકાત બુક કરો|રાખો)"
)


def literal_booking_time(text: str, *, branch: str) -> TimeSlot | None:
    """No fuzzy correction, alternatives, questions, negation or guessed date/time."""
    if not isinstance(text, str) or branch not in BRANCHES or any(c in text for c in "?？"):
        return None
    value = unicodedata.normalize("NFC", text)
    value = "".join(str(unicodedata.decimal(c)) if c.isdecimal() else c for c in value)
    value = " ".join(value.replace(",", " ").split()).rstrip(".। ")
    match = _HINDI.fullmatch(value) or _GUJARATI.fullmatch(value)
    if match is None:
        return None
    named_branch = match.group("branch")
    if named_branch is not None and _BRANCHES[named_branch] != branch:
        return None
    number = match.group("hour")
    hour = int(number) if number.isdecimal() else _HOURS[number]
    return TimeSlot(day_offset=1, hour=hour, period=_PERIODS[match.group("period")],
                    accepted=True, confidence=1.0)
