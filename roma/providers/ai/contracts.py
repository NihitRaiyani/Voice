"""Compatibility exports of the SDK-free core AI contracts."""

from roma.core.ai_contracts import (
    AudioFormat as AudioFormat,
)
from roma.core.ai_contracts import (
    EmbeddingIdentity as EmbeddingIdentity,
)
from roma.core.ai_contracts import (
    EmbeddingProvider as EmbeddingProvider,
)
from roma.core.ai_contracts import (
    EmbeddingRequest as EmbeddingRequest,
)
from roma.core.ai_contracts import (
    EmbeddingResult as EmbeddingResult,
)
from roma.core.ai_contracts import (
    LLMChunk as LLMChunk,
)
from roma.core.ai_contracts import (
    LLMGenerationMetrics as LLMGenerationMetrics,
)
from roma.core.ai_contracts import (
    LLMMessage as LLMMessage,
)
from roma.core.ai_contracts import (
    LLMProvider as LLMProvider,
)
from roma.core.ai_contracts import (
    LLMRequest as LLMRequest,
)
from roma.core.ai_contracts import (
    ProviderUnavailable as ProviderUnavailable,
)
from roma.core.ai_contracts import (
    Role as Role,
)
from roma.core.ai_contracts import (
    STTProvider as STTProvider,
)
from roma.core.ai_contracts import (
    STTRequest as STTRequest,
)
from roma.core.ai_contracts import (
    STTResult as STTResult,
)
from roma.core.ai_contracts import (
    TTSProvider as TTSProvider,
)
from roma.core.ai_contracts import (
    TTSRequest as TTSRequest,
)
from roma.core.ai_contracts import (
    TTSResult as TTSResult,
)

__all__ = [
    "AudioFormat",
    "EmbeddingProvider",
    "EmbeddingIdentity",
    "EmbeddingRequest",
    "EmbeddingResult",
    "LLMChunk",
    "LLMGenerationMetrics",
    "LLMMessage",
    "LLMProvider",
    "LLMRequest",
    "ProviderUnavailable",
    "Role",
    "STTProvider",
    "STTRequest",
    "STTResult",
    "TTSProvider",
    "TTSRequest",
    "TTSResult",
]
