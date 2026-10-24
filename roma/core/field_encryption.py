"""Authenticated selected-field encryption with explicit process activation."""

import base64
import os

from sqlalchemy import Text
from sqlalchemy.types import TypeDecorator

from roma.core.privacy_settings import PrivacySettings


class PrivacyError(RuntimeError):
    """Sanitized privacy failure; never contains input, key or filesystem paths."""


def secret_key(value):
    try:
        raw = bytes.fromhex(value.get_secret_value())
        if len(raw) != 32:
            raise ValueError
        return raw
    except (ValueError, AttributeError):
        raise PrivacyError("A distinct 32-byte hexadecimal privacy key is required.") from None


class FieldCipher:
    prefix = "roma:enc:v1:"

    def __init__(self, settings: PrivacySettings):
        self.enabled = settings.pii_encryption_enabled
        self._key = secret_key(settings.pii_encryption_key) if self.enabled else None

    def encrypt(self, value, *, context):
        if value is None:
            return None
        if not self.enabled:
            if value.startswith(self.prefix):
                raise PrivacyError("Encrypted values require configured field encryption.")
            return value
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM

        nonce = os.urandom(12)
        data = AESGCM(self._key).encrypt(nonce, value.encode("utf-8"), context.encode("ascii"))
        return self.prefix + base64.urlsafe_b64encode(nonce + data).decode("ascii")

    def decrypt(self, value, *, context):
        if value is None or not value.startswith(self.prefix):
            # Legacy plaintext remains readable for an explicit operator backfill.
            return value
        if not self.enabled:
            raise PrivacyError("Encrypted values require configured field encryption.")
        from cryptography.exceptions import InvalidTag
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM

        try:
            raw = base64.b64decode(value[len(self.prefix) :], altchars=b"-_", validate=True)
            return (
                AESGCM(self._key)
                .decrypt(raw[:12], raw[12:], context.encode("ascii"))
                .decode("utf-8")
            )
        except (ValueError, InvalidTag, UnicodeError):
            raise PrivacyError("Encrypted field authentication failed.") from None


_cipher = FieldCipher(PrivacySettings.model_construct(pii_encryption_enabled=False))


def configure_field_encryption(settings: PrivacySettings):
    global _cipher
    _cipher = FieldCipher(settings)


class EncryptedText(TypeDecorator):
    """Encrypt bound values; all ORM and typed SQL writes use the same boundary."""

    impl = Text
    cache_ok = True

    def __init__(self, context):
        super().__init__()
        self.context = context

    def process_bind_param(self, value, dialect):
        return _cipher.encrypt(value, context=self.context)

    def process_result_value(self, value, dialect):
        return _cipher.decrypt(value, context=self.context)
