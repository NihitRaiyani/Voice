"""Code-owned intent routing before any knowledge/model work.

Classification is deliberately conservative. Unknown text is never evidence.
"""

from dataclasses import dataclass
from enum import StrEnum

from roma.domain.conversation.confirmguard import SAFE_SIGNOFF_LINE, lead_wants_out
from roma.domain.conversation.objection import classify_objection
from roma.domain.conversation.shortcircuit import (
    CERT_BRANDS,
    FEE_TOPIC,
    PLACEMENT_TOPIC,
    SALARY_TOPIC,
)
from roma.domain.safety.lexicon import (
    DISCOUNT_KEYWORDS,
    HARD_FAIL_LINE,
    SALARY_ANCHOR,
    SALARY_KEYWORDS,
    SUBSTITUTIONS,
    BlockCategory,
)
from roma.domain.safety.normalize import tokens

NO_EVIDENCE_LINE = "Iski approved jaankari ke liye counselling team se poochh lijiye."


class KnowledgeRoute(StrEnum):
    FIXED = "deterministic"
    STATE = "state"
    BOOKING = "booking"
    SAFETY = "safety"
    KNOWLEDGE = "knowledge"
    NO_EVIDENCE = "no_evidence"


@dataclass(frozen=True)
class RouteDecision:
    route: KnowledgeRoute
    topic: str | None = None
    category: BlockCategory | None = None
    reply: str | None = None


_TOPICS = {
    "course": frozenset(
        {
            "course",
            "courses",
            "module",
            "modules",
            "seo",
            "ads",
            "practical",
            "skills",
            "learn",
            "faculty",
            "teacher",
            "duration",
            "કોર્સ",
            "કોર્સમાં",
            "कोर्स",
            "मॉड्यूल",
            "મોડ્યુલ",
        }
    ),
    "schedule": frozenset(
        {"schedule", "batch", "batches", "online", "offline", "બેચ", "बैच", "સમય"}
    ),
    "branch": frozenset(
        {
            "branch",
            "branches",
            "address",
            "location",
            "campus",
            "સરનામું",
            "શાખામાં",
            "કેમ્પસમાં",
            "शाखा",
            "पता",
            "कैंपस",
            "कैम्पस",
        }
    ),
    "policy": frozenset(
        {
            "policy",
            "policies",
            "certificate",
            "certification",
            "recording",
            "recordings",
            "नीति",
            "નીતિ",
            "નીતિમાં",
            "प्रमाणपत्र",
        }
    ),
}
_DISCOUNTS = frozenset(
    {"discount", "discounts", "scheme", "offer", "छूट", "ડિસ્કાઉન્ટ", "डिस्काउंट"}
)
_BOOKING = frozenset(
    {
        "book",
        "booking",
        "appointment",
        "reschedule",
        "cancel",
        "cancellation",
        "visit",
        "बुक",
        "अपॉइंटमेंट",
        "રદ",
        "મુલાકાત",
    }
)
_QUESTIONS = frozenset(
    {
        "what",
        "why",
        "how",
        "where",
        "when",
        "which",
        "who",
        "kya",
        "kaise",
        "kitna",
        "kitni",
        "क्या",
        "कहाँ",
        "कब",
        "ક્યાં",
        "શું",
        "ક્યારે",
    }
)
# Booking nouns also occur in factual questions. Only an action can make a
# polite question ("Can you book...?") a booking request; information cues win
# over incidental action words ("course duration before I book a visit?").
_BOOKING_ACTIONS = frozenset({"book", "reschedule", "cancel", "बुक", "રદ"})
_BOOKING_INFORMATION = frozenset(
    {
        "policy",
        "policies",
        "requirement",
        "requirements",
        "document",
        "documents",
        "process",
        "procedure",
        "rules",
        "details",
        "information",
        "eligibility",
        "duration",
        "before",
        "after",
        "नीति",
        "નીતિ",
        "નીતિમાં",
    }
)
_INFORMATION_REQUESTS = frozenset(
    {
        "tell",
        "explain",
        "describe",
        "bataiye",
        "batao",
        "bataye",
        "jaanna",
        "janna",
        "samjhaiye",
        "बताइए",
        "बताओ",
        "બતાવો",
        "વિશે",
    }
)
_ACKS = frozenset(
    {
        "yes",
        "no",
        "haan",
        "ha",
        "nahi",
        "okay",
        "ok",
        "hello",
        "hi",
        "हाँ",
        "हां",
        "नहीं",
        "હા",
        "ના",
        "ji",
        "bilkul",
        "achha",
        "accha",
        "samajh",
        "gayi",
        "gaya",
        "theek",
        "thik",
        "hai",
    }
)


def classify_turn(state, text: str, *, deterministic_reply: str | None = None) -> RouteDecision:
    """Protected topics beat topic/booking words; only explicit scopes enter knowledge."""
    if state.lead_wants_out or lead_wants_out(text):
        return RouteDecision(KnowledgeRoute.FIXED, reply=SAFE_SIGNOFF_LINE)
    try:
        words = set(tokens(text))
        category = None
        if words & CERT_BRANDS:
            category = BlockCategory.CERT
        elif words & FEE_TOPIC or words & {"price", "prices", "pricing", "emi", "कीमत", "કિંમત"}:
            category = BlockCategory.FEE
        elif words & (SALARY_TOPIC | SALARY_ANCHOR | SALARY_KEYWORDS):
            category = BlockCategory.SALARY
        elif words & PLACEMENT_TOPIC:
            category = BlockCategory.PLACEMENT
        elif words & (_DISCOUNTS | DISCOUNT_KEYWORDS):
            category = BlockCategory.DISCOUNT
        if category is not None:
            return RouteDecision(
                KnowledgeRoute.SAFETY, category=category, reply=SUBSTITUTIONS[category]
            )
        question = bool(words & (_QUESTIONS | _INFORMATION_REQUESTS)) or "?" in text
        booking_information = bool(words & _BOOKING) and (
            bool(words & (_BOOKING_INFORMATION | _INFORMATION_REQUESTS))
            or bool(words & (_QUESTIONS - {"kya", "क्या", "શું"}))
            or (question and not words & _BOOKING_ACTIONS)
        )
        # A noun-only information request may have no question word/punctuation.
        question = question or booking_information
        if not question and (state.stage == "open" or classify_objection(text) is not None):
            return RouteDecision(KnowledgeRoute.STATE)
        # A profile answer may name a course/batch without asking a factual question.
        if state.stage == "discover" and not question and not words & _BOOKING:
            return RouteDecision(KnowledgeRoute.STATE)
        if words & _BOOKING and not booking_information:
            return RouteDecision(KnowledgeRoute.BOOKING)
        for topic, vocabulary in _TOPICS.items():
            if words & vocabulary:
                return RouteDecision(KnowledgeRoute.KNOWLEDGE, topic=topic)
        if deterministic_reply is not None:
            return RouteDecision(KnowledgeRoute.FIXED, reply=deterministic_reply)
        if state.stage in {"pivot", "close"} and not question:
            return RouteDecision(KnowledgeRoute.BOOKING)
        if (state.stage == "discover" and not question) or words <= _ACKS:
            return RouteDecision(KnowledgeRoute.STATE)
        return RouteDecision(KnowledgeRoute.NO_EVIDENCE, reply=NO_EVIDENCE_LINE)
    except (TypeError, ValueError, AttributeError):
        return RouteDecision(KnowledgeRoute.NO_EVIDENCE, reply=HARD_FAIL_LINE)


# Exact reviewed course facts already present in prompts/stages/{value,structure}.md.
# This is a bounded static lookup, not semantic retrieval or model knowledge.
_STATIC_FACTS = (
    (
        frozenset({"module", "modules", "syllabus", "seo", "मॉड्यूल", "મોડ્યુલ"}),
        "modules",
        "SEO, social media marketing aur Google Ads, teen module hain.",
    ),
    (
        frozenset({"duration", "long", "mahine", "अवधि", "સમયગાળો"}),
        "duration",
        "Course chaar se chhe mahine ka hai, basic se advance level tak.",
    ),
    (
        frozenset({"faculty", "teacher", "teaching", "फैकल्टी"}),
        "faculty",
        "Faculty working professionals hain, abhi industry mein kaam kar rahe hain.",
    ),
    (
        frozenset({"practical", "project", "projects", "प्रैक्टिकल"}),
        "practical",
        "Practical campaign work aur live projects se portfolio banta hai.",
    ),
    (
        frozenset({"recording", "recordings", "revision", "रिकॉर्डिंग"}),
        "recording",
        "Lecture recordings aur revision batches available hain.",
    ),
    (
        frozenset({"online", "offline"}),
        "schedule",
        "Online ya offline, same class aur same faculty hain. Exact batch visit par decide hota hai.",
    ),
    (
        frozenset({"schedule", "batch", "batches", "બેચ", "बैच"}),
        "schedule",
        "Morning, evening aur weekend batches hain. Exact batch visit par decide hota hai.",
    ),
    (
        frozenset({"certificate", "certification", "प्रमाणपत्र"}),
        "certificate",
        "Weltec ka certificate milta hai.",
    ),
)


def approved_static_reply(state, text: str) -> str:
    """Use a matching reviewed fact once, otherwise defer without a generated claim."""
    words = set(tokens(text))
    if state.course != "digital_marketing":
        return NO_EVIDENCE_LINE
    # Static evidence covers only this course. Unknown qualifiers cannot borrow it.
    allowed = set().union(*_TOPICS.values(), *(row[0] for row in _STATIC_FACTS), _QUESTIONS)
    allowed.update(
        {
            "digital",
            "marketing",
            "weltec",
            "the",
            "a",
            "an",
            "is",
            "are",
            "does",
            "do",
            "you",
            "your",
            "i",
            "my",
            "me",
            "tell",
            "about",
            "explain",
            "please",
            "provide",
            "teach",
            "taught",
            "for",
            "in",
            "of",
            "and",
            "or",
            "will",
            "can",
            "it",
            "there",
            "ka",
            "ki",
            "ke",
            "ko",
            "hai",
            "hain",
            "hota",
            "hoti",
            "hote",
            "kitne",
            "mahine",
            "kaun",
            "bataiye",
            "batao",
            "baare",
            "mein",
            "kya",
            "mujhe",
            "se",
            "aap",
            "kaisa",
            "kaise",
            "milta",
            "milti",
            "है",
            "हैं",
            "का",
            "की",
            "के",
            "में",
            "कितने",
            "માટે",
            "છે",
            "કેટલા",
        }
    )
    if words - allowed:
        return NO_EVIDENCE_LINE
    for cues, fact, line in _STATIC_FACTS:
        if words & cues:
            if fact in state.facts_said:
                return NO_EVIDENCE_LINE
            return line
    return NO_EVIDENCE_LINE
