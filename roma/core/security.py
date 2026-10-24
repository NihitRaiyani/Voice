"""Library-backed password and fixed-algorithm access-token primitives."""

from datetime import UTC, datetime
from uuid import UUID, uuid4

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError

from roma.core.auth_settings import AuthSettings


class Passwords:
    def __init__(self):
        self.hasher = PasswordHasher()

    def hash(self, password: str) -> str:
        if (
            not isinstance(password, str)
            or not 12 <= len(password) <= 256
            or len(password.encode("utf-8")) > 1024
        ):
            raise ValueError("Password must contain 12..256 characters and at most 1024 bytes")
        return self.hasher.hash(password)

    def verify(self, encoded: str, password: str) -> bool:
        try:
            if (
                not isinstance(password, str)
                or not 1 <= len(password) <= 256
                or len(password.encode("utf-8")) > 1024
            ):
                return False
            return self.hasher.verify(encoded, password)
        except (InvalidHashError, VerificationError, UnicodeError):
            return False


class AccessTokens:
    def __init__(self, settings: AuthSettings):
        if settings.auth_jwt_secret is None:
            raise ValueError("Human authentication is not configured")
        self.settings = settings
        self._key = settings.auth_jwt_secret.get_secret_value()

    def issue(self, user_id: UUID, session_id: UUID) -> str:
        now = int(datetime.now(UTC).timestamp())
        return jwt.encode(
            {
                "sub": str(user_id),
                "sid": str(session_id),
                "jti": str(uuid4()),
                "iat": now,
                "nbf": now,
                "exp": now + self.settings.auth_access_ttl_secs,
                "iss": self.settings.auth_jwt_issuer,
                "aud": self.settings.auth_jwt_audience,
            },
            self._key,
            algorithm="HS256",
            headers={"typ": "JWT"},
        )

    def decode(self, token: str) -> dict:
        if not isinstance(token, str) or not token.isascii() or not 1 <= len(token) <= 4096:
            raise ValueError("Invalid access token")
        try:
            if jwt.get_unverified_header(token).get("typ") != "JWT":
                raise ValueError("Invalid token type")
            claims = jwt.decode(
                token,
                self._key,
                algorithms=["HS256"],
                issuer=self.settings.auth_jwt_issuer,
                audience=self.settings.auth_jwt_audience,
                options={
                    "require": ["sub", "sid", "jti", "iat", "nbf", "exp", "iss", "aud"],
                    "strict_aud": True,
                },
            )
            for key in ("sub", "sid", "jti"):
                if not isinstance(claims[key], str):
                    raise ValueError("Invalid token identifier")
                UUID(claims[key])
            for key in ("iat", "nbf", "exp"):
                if type(claims[key]) is not int:
                    raise ValueError("Invalid token timestamp")
            if (
                claims["exp"] <= claims["iat"]
                or claims["exp"] - claims["iat"] > self.settings.auth_access_ttl_secs
            ):
                raise ValueError("Invalid token lifetime")
            return claims
        except (jwt.InvalidTokenError, TypeError, KeyError) as exc:
            raise ValueError("Invalid access token") from exc
