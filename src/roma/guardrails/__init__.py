"""Roma pre-TTS guardrail filter (docs/04-guardrails.md).

No callable build ships without this. The TTS path calls `safe_output`.
"""

from roma.guardrails.filter import FilterVerdict, safe_output, screen
from roma.guardrails.lexicon import BlockCategory

__all__ = ["screen", "safe_output", "FilterVerdict", "BlockCategory"]
