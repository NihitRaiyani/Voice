"""Finite pronunciation bindings for the unchanged, approved Roman safety lines.

These are Hindi-base Hinglish phonetic renderings, never a general translator or
transliterator. Exact canonical wording and both SHA256 identities are required;
lexicon changes invalidate the old rendering until it is explicitly reviewed.
"""

from dataclasses import dataclass
from hashlib import sha256

from roma.domain.safety import lexicon

PRONUNCIATION_VERSION = "roma-safety-pronunciation-hi-v1"


@dataclass(frozen=True)
class SafetyPronunciation:
    name: str
    canonical_text: str
    canonical_sha256: str
    spoken_text: str
    spoken_sha256: str
    language: str = "hi"
    version: str = PRONUNCIATION_VERSION

    @property
    def identity(self) -> tuple[str, ...]:
        return (self.version, self.canonical_sha256, self.spoken_sha256, self.language)


# Literal text + independently pinned digests: do not derive a new approval from
# the current lexicon value. FEE and DISCOUNT deliberately share one binding.
PRONUNCIATIONS = (
    SafetyPronunciation(
        "fees",
        "Fees aapki situation ke hisaab se counselling meeting mein discuss hogi — isiliye visit best rahega.",
        "52f33890d08d9d66715fbc445134eeb82d6b44fbdc34186bf8527332fe5d08d2",
        "फीस आपकी सिचुएशन के हिसाब से काउन्सलिंग मीटिंग में डिस्कस होगी — इसीलिए विजिट बेस्ट रहेगा।",
        "a8971e17a85c2e64c18b9e9a6d685738df23c94ad14e8e1ed5f99061403ed769",
    ),
    SafetyPronunciation(
        "salary",
        "Outcome aapke effort aur interview pe depend karta hai — hum skills aur placement support dete hain.",
        "1344016b3b3bd949ecdb8b622c6398b0dc8be9f2ce5b974f07bba2580176e758",
        "आउटकम आपके एफर्ट और इंटरव्यू पे डिपेंड करता है — हम स्किल्स और प्लेसमेंट सपोर्ट देते हैं।",
        "0c099da2f01782163ef548d7d0856835b91b4515ca2944712fb88c5ccfe12c53",
    ),
    SafetyPronunciation(
        "placement",
        "Placement support institute provide karta hai. Counsellor visit ke dauran complete placement process aur opportunities explain karenge.",
        "d4843ff0062a8981517992163864cba0f121c08de8faa58b99c3fce34ecc422b",
        "प्लेसमेंट सपोर्ट इंस्टिट्यूट प्रोवाइड करता है। काउन्सलर विजिट के दौरान कम्प्लीट प्लेसमेंट प्रोसेस और अपॉर्च्युनिटीज़ एक्सप्लेन करेंगे।",
        "012a9295cc9a72f56f7763ab434b7fb366f6487fbb6dd3c82fd2006ee09eb97d",
    ),
    SafetyPronunciation(
        "certificate",
        "Certificate ke baare mein counsellor visit pe detail batayenge — hum Weltec ka certificate dete hain.",
        "25b5bc195e6d0ae66331f0c9f7cf6b2bb50b407d397ca3e49463b3729090758e",
        "सर्टिफिकेट के बारे में काउन्सलर विजिट पे डिटेल बताएंगे — हम वेलटेक का सर्टिफिकेट देते हैं।",
        "a59c03373c9b6a09b8a8bd9df291981f9adbf9585e66f8efa24137ceee8bf2c5",
    ),
    SafetyPronunciation(
        "hard_fail",
        "Ye detail counsellor visit pe batayenge.",
        "dc669426b4f8671ea967516a078da933f76c21f801e0c934d81e94c3fdd99967",
        "ये डिटेल काउन्सलर विजिट पे बताएंगे।",
        "38c737fbec86b6c7908329ea8b83577de8ac0a336f148a1edc986f17c7a73b17",
    ),
    SafetyPronunciation(
        "permitted_emi",
        "No-cost EMI available hai.",
        "b0f02b0bd63daad3960229063c36b5133707bda0638ec0129f117d82e126c252",
        "नो-कॉस्ट ई एम आई अवेलेबल है।",
        "e1fd1d00e97d1865a7e673949429dd2154c34b1c6cb0cd25562da40451df5670",
    ),
)


def pronunciation_for(canonical_text: str) -> SafetyPronunciation | None:
    """Resolve only exact, still-approved canonical text with unchanged bindings."""
    approved = {
        *lexicon.SUBSTITUTIONS.values(),
        lexicon.HARD_FAIL_LINE,
        lexicon.PERMITTED_MONEY_LINE,
    }
    for item in PRONUNCIATIONS:
        if item.canonical_text != canonical_text:
            continue
        if (
            canonical_text not in approved
            or sha256(canonical_text.encode()).hexdigest() != item.canonical_sha256
            or sha256(item.spoken_text.encode()).hexdigest() != item.spoken_sha256
            or item.language != "hi"
            or item.version != PRONUNCIATION_VERSION
        ):
            raise ValueError("Approved safety pronunciation binding is invalid")
        return item
    if canonical_text in approved:
        raise ValueError("Approved safety wording needs a reviewed pronunciation binding")
    return None


def validate_pronunciation(item: SafetyPronunciation) -> None:
    """Reject caller-created, stale or mutated rendering records before any audio."""
    if pronunciation_for(item.canonical_text) != item:
        raise ValueError("Untrusted safety pronunciation binding")
