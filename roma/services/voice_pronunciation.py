"""Finite local pronunciations of existing controller wording, never arbitrary translation."""

from datetime import datetime
from uuid import UUID

from roma.domain.appointments.booking import (
    BOOKING_UNAVAILABLE_LINE,
    SLOT_CONFLICT_LINE,
    confirmed_line,
)
from roma.domain.audio.speech_normalization import normalize_tts_text
from roma.domain.conversation.confirmguard import (
    SAFE_READBACK_HOLD_LINE,
    SAFE_REOFFER_LINE,
    SAFE_REOFFER_LINE_IN_OFFER,
    SAFE_SIGNOFF_LINE,
)
from roma.domain.conversation.state import spoken_slot
from roma.services.text_conversation_service import DISCOVERY_QUESTIONS

# Exact pronunciations of the same existing questions. Independent final safety
# screening remains in SpeechSynthesisService; these cannot unlock safety exceptions.
_FIXED_QUESTIONS = {
    "Aapka naam kya hai?": "आपका नाम क्या है?",
    "Aap abhi padh rahe hain ya job kar rahe hain?": "आप अभी पढ़ रहे हैं या जॉब कर रहे हैं?",
    "Aap kya padh rahe hain, ya kya padha hai?": "आप क्या पढ़ रहे हैं, या क्या पढ़ा है?",
    "Aapki padhai kis saal poori hogi, ya hui thi?": "आपकी पढ़ाई किस साल पूरी होगी, या हुई थी?",
    "Aap kis city se hain?": "आप किस सिटी से हैं?",
}


_STATIC = (
    (
        "Abhi wo time final nahi hua hai. Aapko subah ka time theek rahega ya shaam ka?",
        SAFE_REOFFER_LINE,
        "अभी वो टाइम फाइनल नहीं हुआ है। आपको सुबह का टाइम ठीक रहेगा या शाम का?",
    ),
    (
        "Abhi wo time final nahi hua hai.",
        SAFE_REOFFER_LINE_IN_OFFER,
        "अभी वो टाइम फाइनल नहीं हुआ है।",
    ),
    (
        "Abhi visit ka time confirm nahi hua hai. Counsellor se sahi availability check karwa dete hain.",
        BOOKING_UNAVAILABLE_LINE,
        "अभी विजिट का टाइम कन्फर्म नहीं हुआ है। काउन्सलर से सही अवेलेबिलिटी चेक करवा देते हैं।",
    ),
    (
        "Woh visit time ab uplabdh nahi hai. Doosra available time dekh lete hain.",
        SLOT_CONFLICT_LINE,
        "वो विजिट टाइम अब उपलब्ध नहीं है। दूसरा अवेलेबल टाइम देख लेते हैं।",
    ),
    (
        "Bilkul, main samajh gayi. Aapka time lene ke liye shukriya — aapka din shubh rahe.",
        SAFE_SIGNOFF_LINE,
        "बिल्कुल, मैं समझ गई। आपका टाइम लेने के लिए शुक्रिया — आपका दिन शुभ रहे।",
    ),
)
_DAYS = dict(
    zip(
        ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"),
        ("मंडे", "ट्यूज़डे", "वेडनेसडे", "थर्सडे", "फ्राइडे", "सैटरडे", "संडे"),
        strict=True,
    )
)
_MONTHS = dict(
    zip(
        (
            "January",
            "February",
            "March",
            "April",
            "May",
            "June",
            "July",
            "August",
            "September",
            "October",
            "November",
            "December",
        ),
        (
            "जनवरी",
            "फरवरी",
            "मार्च",
            "अप्रैल",
            "मे",
            "जून",
            "जुलाई",
            "ऑगस्ट",
            "सेप्टेम्बर",
            "ऑक्टोबर",
            "नवेम्बर",
            "डिसेम्बर",
        ),
        strict=True,
    )
)


def _slot(iso):
    if not isinstance(iso, str) or len(iso) > 64:
        raise ValueError("Invalid controller slot")
    parsed = datetime.fromisoformat(iso)
    if parsed.tzinfo is None:
        raise ValueError("Controller slot needs its timezone")
    # Consume the intact AM/PM clock before finite script substitutions can
    # hide its meridian from the final TTS normalization pass.
    line = normalize_tts_text(spoken_slot(iso), "hi")
    for original, native in {**_DAYS, **_MONTHS}.items():
        line = line.replace(original, native)
    if any(c.isascii() and c.isalpha() for c in line):
        raise ValueError("Unmapped slot pronunciation")
    return line


def render_voice_line(canonical, state):
    if canonical in _FIXED_QUESTIONS:
        if canonical not in DISCOVERY_QUESTIONS.values():
            raise ValueError("Stale local discovery pronunciation")
        return _FIXED_QUESTIONS[canonical]
    for pinned, current, native in _STATIC:
        if canonical == pinned and pinned == current:
            return native
    # Reconstruct the exact expected controller output before rendering any slot.
    # Branches are a closed list, and confirmation still requires SQL-owned state.
    if state.booking_required and state.appointment_id and state.locked_slot:
        UUID(state.appointment_id)
        branches = {"Vadodara": "वडोदरा", "Ahmedabad": "अहमदाबाद"}
        if state.branch not in branches:
            raise ValueError("Unapproved branch pronunciation")
        pinned = f"Aapki {state.branch} branch visit {spoken_slot(state.locked_slot)} ke liye confirm ho gayi hai. Shukriya, milte hain."
        if canonical == pinned == confirmed_line(spoken_slot(state.locked_slot), state.branch):
            return f"आपकी {branches[state.branch]} ब्रांच विजिट {_slot(state.locked_slot)} के लिए कन्फर्म हो गई है। शुक्रिया, मिलते हैं।"
    if (
        SAFE_READBACK_HOLD_LINE == "{slot} — yeh sahi rahega aapke liye?"
        and state.accepted_slot
        and canonical == SAFE_READBACK_HOLD_LINE.format(slot=spoken_slot(state.accepted_slot))
    ):
        return f"{_slot(state.accepted_slot)} — ये सही रहेगा आपके लिए?"
    if state.booking_required and not state.accepted_slot and state.stage in {"pivot", "close"}:
        slots = [iso for iso in state.slots_offered if spoken_slot(iso)][:2]
        prefix = SAFE_REOFFER_LINE_IN_OFFER + " " if state.booking_error else ""
        expected = (
            prefix
            + " ya ".join(spoken_slot(iso) for iso in slots)
            + " — aapke liye kaunsa time sahi rahega?"
        )
        if slots and canonical == expected:
            spoken_prefix = "अभी वो टाइम फाइनल नहीं हुआ है। " if state.booking_error else ""
            return (
                spoken_prefix
                + " या ".join(_slot(iso) for iso in slots)
                + " — आपके लिए कौन सा टाइम सही रहेगा?"
            )
    # Approved safety fallbacks retain canonical identity for their stronger binding
    # checks in SpeechSynthesisService. Unmapped/unsupported wording fails there.
    return canonical


# Exact controller facts, with independently screened native pronunciation. A source
# edit invalidates its literal key rather than authorizing arbitrary transliteration.
_FACT_VOICE = {
    "SEO, social media marketing aur Google Ads, teen module hain.": "एस ई ओ, सोशल मीडिया मार्केटिंग और गूगल ऐड्स, तीन मॉड्यूल हैं।",
    "Course chaar se chhe mahine ka hai, basic se advance level tak.": "कोर्स चार से छह महीने का है, बेसिक से एडवांस लेवल तक।",
    "Faculty working professionals hain, abhi industry mein kaam kar rahe hain.": "फैकल्टी वर्किंग प्रोफेशनल्स हैं, अभी इंडस्ट्री में काम कर रहे हैं।",
    "Practical campaign work aur live projects se portfolio banta hai.": "प्रैक्टिकल कैंपेन वर्क और लाइव प्रोजेक्ट्स से पोर्टफोलियो बनता है।",
    "Lecture recordings aur revision batches available hain.": "लेक्चर रिकॉर्डिंग्स और रिविज़न बैचेज़ अवेलेबल हैं।",
    "Online ya offline, same class aur same faculty hain. Exact batch visit par decide hota hai.": "ऑनलाइन या ऑफलाइन, सेम क्लास और सेम फैकल्टी हैं। एक्ज़ैक्ट बैच विजिट पर डिसाइड होता है।",
    "Morning, evening aur weekend batches hain. Exact batch visit par decide hota hai.": "मॉर्निंग, ईवनिंग और वीकेंड बैचेज़ हैं। एक्ज़ैक्ट बैच विजिट पर डिसाइड होता है।",
    "Weltec ka certificate milta hai.": "वेल्टेक का सर्टिफिकेट मिलता है।",
}


def render_voice_sentence(canonical, state):
    """Render only pinned full lines or their aligned sentence pieces, never a guess."""
    import re

    from roma.domain.knowledge.routing import _STATIC_FACTS, NO_EVIDENCE_LINE

    if canonical == NO_EVIDENCE_LINE == "Iski approved jaankari ke liye counselling team se poochh lijiye.":
        return "इसकी अप्रूव्ड जानकारी के लिए काउन्सलिंग टीम से पूछ लीजिए।"
    rendered = render_voice_line(canonical, state)
    if rendered != canonical:
        return rendered
    pairs = [(line, _FACT_VOICE[line]) for _, _, line in _STATIC_FACTS if line in _FACT_VOICE]
    pairs.extend((line, native) for pinned, line, native in _STATIC if pinned == line)
    if state.booking_required and state.appointment_id and state.locked_slot:
        complete = confirmed_line(spoken_slot(state.locked_slot), state.branch)
        native = render_voice_line(complete, state)
        if native != complete:
            pairs.append((complete, native))
    for original, native in pairs:
        if canonical == original:
            return native
        source_parts = re.split(r"(?<=[.!?])\s+", original)
        native_parts = re.split(r"(?<=[।!?])\s+", native)
        if len(source_parts) == len(native_parts):
            for source, target in zip(source_parts, native_parts, strict=True):
                if canonical.strip() == source:
                    return target
    return canonical
