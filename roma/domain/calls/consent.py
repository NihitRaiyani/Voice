"""The recording-consent disclosure line (docs/07 §consent).

`docs/07` requires recording to be **stated at call start**. This is the line the
pre-dial gate hands back and the Step-1 dialer MUST speak first, before anything
else. The human counsellors already open this way; it doubles as a fee-deflection
anchor (see hard_rules.md register).

The wording is a PLACEHOLDER pending Weltec compliance sign-off. Only the exact
text is a TODO — the structural home (a fixed constant the gate returns) is final.
When Weltec confirms the disclosure wording, replace CONSENT_LINE below; no code
change is needed anywhere else. Keep it short, warm, code-mix, no digits spelled
as numerals (TTS reads them unpredictably — docs/02).
"""

CONSENT_PENDING_MARKER = "[PENDING WELTEC COMPLIANCE]"
CONSENT_LINE = f"{CONSENT_PENDING_MARKER} recording disclosure line"


def consent_signed_off(line: str = CONSENT_LINE) -> bool:
    """True once the disclosure wording is real, i.e. no longer the placeholder.

    Storing a call recording is only lawful if the lead was actually told they were being
    recorded. While CONSENT_LINE is a placeholder, no lead has been told anything, so the
    post-call worker refuses to persist the audio (docs/07 fail-safe: when a guardrail
    dependency is unavailable, stop rather than proceed unguarded).

    A marker check, not an equality check, so the module docstring's promise holds
    literally: replace CONSENT_LINE and nothing else needs editing — including this.
    """
    return bool(line) and CONSENT_PENDING_MARKER not in line


__all__ = ["CONSENT_LINE", "CONSENT_PENDING_MARKER", "consent_signed_off"]
