"""Compatibility imports for the preserved G.711 codec.

Section 14 places the implementation in the dependency-free audio domain. Existing
clip callers retain odd-tail truncation; new streaming code uses g711.encode(),
which rejects partial PCM16 samples. Existing asset bytes are unchanged.
"""

from roma.domain.audio.g711 import pcm16_to_ulaw, ulaw_to_pcm16

__all__ = ["pcm16_to_ulaw", "ulaw_to_pcm16"]
