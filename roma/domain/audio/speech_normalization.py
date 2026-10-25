"""Bounded speech rendering, never translation or arbitrary transliteration.

Only declared ISO/Indian dates, clock times and integers below 10,000 are
expanded. Four-digit calendar years use two-digit groups (2026: twenty twenty
six), with zero-prefixed suffixes spoken explicitly. This avoids amount-scale
words in calendar readbacks without granting any safety exception.
This module grants no safety permission: callers must screen both the
source and the resulting speech. Native listening approval remains separate.
"""

import re
import unicodedata
from datetime import date

NORMALIZATION_VERSION = "roma-tts-normalization-v2"
_SMALL = {
    "en": "zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen seventeen eighteen nineteen".split(),
    "hi": (
        "शून्य एक दो तीन चार पाँच छह सात आठ नौ दस ग्यारह बारह तेरह चौदह पंद्रह सोलह सत्रह अठारह उन्नीस "
        "बीस इक्कीस बाईस तेईस चौबीस पच्चीस छब्बीस सत्ताईस अट्ठाईस उनतीस "
        "तीस इकतीस बत्तीस तैंतीस चौंतीस पैंतीस छत्तीस सैंतीस अड़तीस उनतालीस "
        "चालीस इकतालीस बयालीस तैंतालीस चवालीस पैंतालीस छियालीस सैंतालीस अड़तालीस उनचास "
        "पचास इक्यावन बावन तिरपन चौवन पचपन छप्पन सत्तावन अट्ठावन उनसठ "
        "साठ इकसठ बासठ तिरसठ चौंसठ पैंसठ छियासठ सड़सठ अड़सठ उनहत्तर "
        "सत्तर इकहत्तर बहत्तर तिहत्तर चौहत्तर पचहत्तर छिहत्तर सतहत्तर अठहत्तर उनासी "
        "अस्सी इक्यासी बयासी तिरासी चौरासी पचासी छियासी सत्तासी अट्ठासी नवासी "
        "नब्बे इक्यानवे बानवे तिरानवे चौरानवे पंचानवे छियानवे सत्तानवे अट्ठानवे निन्यानवे"
    ).split(),
    "gu": (
        "શૂન્ય એક બે ત્રણ ચાર પાંચ છ સાત આઠ નવ દસ અગિયાર બાર તેર ચૌદ પંદર સોળ સત્તર અઢાર ઓગણીસ "
        "વીસ એકવીસ બાવીસ તેવીસ ચોવીસ પચ્ચીસ છવ્વીસ સત્તાવીસ અઠ્ઠાવીસ ઓગણત્રીસ "
        "ત્રીસ એકત્રીસ બત્રીસ તેત્રીસ ચોત્રીસ પાંત્રીસ છત્રીસ સાડત્રીસ આડત્રીસ ઓગણચાલીસ "
        "ચાલીસ એકતાલીસ બેતાલીસ તેતાલીસ ચુમ્માલીસ પિસ્તાલીસ છેતાલીસ સુડતાલીસ અડતાલીસ ઓગણપચાસ "
        "પચાસ એકાવન બાવન ત્રેપન ચોપન પંચાવન છપ્પન સત્તાવન અઠ્ઠાવન ઓગણસાઠ "
        "સાઠ એકસઠ બાસઠ ત્રેસઠ ચોસઠ પાંસઠ છાસઠ સડસઠ અડસઠ ઓગણસિત્તેર "
        "સિત્તેર એકોતેર બોતેર તોતેર ચુમ્મોતેર પંચોતેર છોતેર સિત્યોતેર ઇઠ્યોતેર ઓગણાએંસી "
        "એંસી એક્યાસી બ્યાસી ત્ર્યાસી ચોર્યાસી પંચ્યાસી છ્યાસી સત્ત્યાસી અઠ્યાસી નેવ્યાસી "
        "નેવું એકાણું બાણું ત્રાણું ચોરાણું પંચાણું છન્નું સત્તાણું અઠ્ઠાણું નવ્વાણું"
    ).split(),
}
_TENS_EN = "zero ten twenty thirty forty fifty sixty seventy eighty ninety".split()
_SCALES = {"en": ("hundred", "thousand"), "hi": ("सौ", "हज़ार"), "gu": ("સો", "હજાર")}
_MONTHS = {
    "en": "January February March April May June July August September October November December".split(),
    "hi": "जनवरी फरवरी मार्च अप्रैल मई जून जुलाई अगस्त सितंबर अक्टूबर नवंबर दिसंबर".split(),
    "gu": "જાન્યુઆરી ફેબ્રુઆરી માર્ચ એપ્રિલ મે જૂન જુલાઈ ઓગસ્ટ સપ્ટેમ્બર ઓક્ટોબર નવેમ્બર ડિસેમ્બર".split(),
}
_TERMS = {
    "en": {"SEO": "S E O", "SEM": "S E M", "PPC": "P P C", "CRM": "C R M"},
    "hi": {
        "SEO": "एस ई ओ", "SEM": "एस ई एम", "PPC": "पी पी सी", "CRM": "सी आर एम",
        "Digital Marketing": "डिजिटल मार्केटिंग", "Google Ads": "गूगल ऐड्स",
        "appointment": "अपॉइंटमेंट", "branch": "ब्रांच", "slot": "स्लॉट",
        "slots": "स्लॉट्स", "confirm": "कन्फर्म",
    },
    "gu": {
        "SEO": "એસ ઈ ઓ", "SEM": "એસ ઈ એમ", "PPC": "પી પી સી", "CRM": "સી આર એમ",
        "Digital Marketing": "ડિજિટલ માર્કેટિંગ", "Google Ads": "ગૂગલ એડ્સ",
        "appointment": "અપોઇન્ટમેન્ટ", "branch": "બ્રાન્ચ", "slot": "સ્લોટ",
        "slots": "સ્લોટ્સ", "confirm": "કન્ફર્મ",
    },
}
_DATE = re.compile(r"(?<!\w)(?:[0-9]{4}-[0-9]{2}-[0-9]{2}|[0-9]{2}/[0-9]{2}/[0-9]{4})(?!\w)")
_TIME = re.compile(r"(?<!\w)([0-9]{1,2}):([0-9]{2})(?:\s*([AP]M))?(?!\w)", re.IGNORECASE)


def _cardinal(value: int, lang: str) -> str:
    if not 0 <= value < 10000:
        raise ValueError("Speech number exceeds supported range")
    if value < len(_SMALL[lang]):
        return _SMALL[lang][value]
    if value < 100:
        tens, units = divmod(value, 10)
        return _TENS_EN[tens] + (" " + _SMALL[lang][units] if units else "")
    scale = 1000 if value >= 1000 else 100
    head, tail = divmod(value, scale)
    result = f"{_cardinal(head, lang)} {_SCALES[lang][scale == 1000]}"
    return result + (" " + _cardinal(tail, lang) if tail else "")


def _clock(hour: int, minute: int, lang: str) -> str:
    if lang == "en":
        period = "at night" if hour < 5 or hour >= 21 else "in the morning" if hour < 12 else "in the afternoon" if hour < 17 else "in the evening"
        minutes = "o'clock" if not minute else "oh " + _cardinal(minute, lang) if minute < 10 else _cardinal(minute, lang)
        return f"{_cardinal(hour % 12 or 12, lang)} {minutes} {period}"
    period = (
        ("रात" if hour < 5 or hour >= 21 else "सुबह" if hour < 12 else "दोपहर" if hour < 17 else "शाम")
        if lang == "hi" else
        ("રાત્રે" if hour < 5 or hour >= 21 else "સવારે" if hour < 12 else "બપોરે" if hour < 17 else "સાંજે")
    )
    current = _cardinal(hour % 12 or 12, lang)
    ending = "बजे" if lang == "hi" else "વાગ્યે"
    if minute in (0, 15, 30, 45):
        if minute == 45:
            current = _cardinal((hour + 1) % 12 or 12, lang)
        prefixes = {"hi": {0: "", 15: "सवा ", 30: "साढ़े ", 45: "पौने "},
                    "gu": {0: "", 15: "સવા ", 30: "સાડા ", 45: "પોણા "}}
        # Half past one/two have dedicated everyday forms in both languages.
        half = {"hi": {1: "डेढ़", 2: "ढाई"}, "gu": {1: "દોઢ", 2: "અઢી"}}
        wording = half[lang].get(hour % 12, "") if minute == 30 else ""
        return f"{period} {wording or prefixes[lang][minute] + current} {ending}"
    return (f"{period} {current} बजकर {_cardinal(minute, lang)} मिनट" if lang == "hi"
            else f"{period} {current} વાગીને {_cardinal(minute, lang)} મિનિટ")


def _calendar_year(year: int, lang: str) -> str:
    if year < 1000:
        return _cardinal(year, lang)
    century, suffix = divmod(year, 100)
    tail = (_cardinal(suffix, lang) if suffix >= 10 else
            f"{_SMALL[lang][0]} {_SMALL[lang][suffix]}")
    return f"{_cardinal(century, lang)} {tail}"


def normalize_tts_text(text: str, language: str) -> str:
    """Render only finite vocabulary, declared dates/times and bounded cardinals."""
    if type(language) is not str or language not in {
        "hi", "hi-IN", "gu", "gu-IN", "en", "en-IN", "en-US", "en-GB",
    }:
        raise ValueError("Speech normalization requires an explicitly supported language")
    lang = language.split("-")[0]
    text = unicodedata.normalize("NFC", text)
    if any(unicodedata.category(c) == "Cf" or
           (unicodedata.category(c) == "Cc" and not c.isspace()) for c in text):
        raise ValueError("Control characters are not accepted for speech")
    # Validate complete numeric forms before replacement can conceal punctuation
    # or split a private number into individually permissible short cardinals.
    if re.search(r"[+−]\s*\d|(?<!\d)-\s*\d", text):
        raise ValueError("Signed speech numbers are not supported")
    for candidate in re.finditer(r"\d+(?:[-/:.,]\d+)+", text):
        if not (_DATE.fullmatch(candidate.group()) or _TIME.fullmatch(candidate.group())):
            raise ValueError("Unsupported speech date, time or numeric form")
    for clock in _TIME.finditer(text):
        if re.match(r"\s*(?:[AP]\.M\.?|[A-Z]{1,3})\b", text[clock.end():]):
            raise ValueError("Unsupported speech time marker")

    def render_date(match: re.Match) -> str:
        raw = match.group()
        if "-" in raw:
            year, month, day = map(int, raw.split("-"))
        else:
            day, month, year = map(int, raw.split("/"))
        date(year, month, day)
        return f"{_cardinal(day, lang)} {_MONTHS[lang][month - 1]} {_calendar_year(year, lang)}"

    def render_time(match: re.Match) -> str:
        hour, minute = int(match[1]), int(match[2])
        meridian = match[3]
        if minute > 59 or (not 1 <= hour <= 12 if meridian else hour > 23):
            raise ValueError("Invalid speech time")
        if meridian:
            hour = hour % 12 + (12 if meridian.upper() == "PM" else 0)
        return _clock(hour, minute, lang)

    text = _DATE.sub(render_date, text)
    text = _TIME.sub(render_time, text)
    if re.search(r"\d\s*[:/]|[:/]\s*\d", text):
        raise ValueError("Unsupported speech date or time")
    if re.search(r"(?<=[^\W\d_])\d|\d(?=[^\W\d_])", text):
        raise ValueError("Embedded speech numbers are not supported")
    for group in re.finditer(r"\d(?:[\d\s().,-]*\d)?", text):
        if sum(c.isdecimal() for c in group.group()) >= 7:
            raise ValueError("Private or long speech numbers are not accepted")
    for original, spoken in _TERMS[lang].items():
        text = re.sub(r"(?<!\w)" + re.escape(original) + r"(?!\w)", spoken, text,
                      flags=re.IGNORECASE)
    def render_number(match: re.Match) -> str:
        raw = match.group()
        if len(raw) > 4 or (len(raw) > 1 and unicodedata.decimal(raw[0]) == 0):
            raise ValueError("Speech number exceeds supported range")
        return _cardinal(int(raw), lang)

    text = re.sub(r"\d+", render_number, text)
    return " ".join(text.split())
