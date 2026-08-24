"""Encryption at rest for the Google refresh token.

A refresh token is a long-lived credential to a CU staff member's Drive. It is
the one value in this database that is worth stealing, so it is the one value
that is never stored in the clear — not in a file, not in the repo, not in a
plain column. The key lives only in the environment.
"""

from __future__ import annotations

import sys

from cryptography.fernet import Fernet, InvalidToken

from .errors import ConfigError

__all__ = ["generate_key", "encrypt_secret", "decrypt_secret"]


def generate_key() -> str:
    """Return a new URL-safe base64 Fernet key."""
    return Fernet.generate_key().decode("ascii")


def _cipher(key: str) -> Fernet:
    try:
        return Fernet(key.encode("ascii") if isinstance(key, str) else key)
    except (ValueError, TypeError) as exc:
        raise ConfigError(
            "CUFA_ENCRYPTION_KEY is not a valid Fernet key. Generate one with:\n"
            "    python -m cufa.crypto keygen"
        ) from exc


def encrypt_secret(plaintext: str, key: str) -> bytes:
    """Encrypt a secret for storage in a bytea column."""
    return _cipher(key).encrypt(plaintext.encode("utf-8"))


def decrypt_secret(ciphertext: bytes | memoryview, key: str) -> str:
    """Decrypt a stored secret.

    A wrong key raises rather than returning garbage: silently returning a
    corrupt token would surface much later as an opaque Google auth failure.
    """
    raw = bytes(ciphertext)
    try:
        return _cipher(key).decrypt(raw).decode("utf-8")
    except InvalidToken as exc:
        raise ConfigError(
            "Stored credential could not be decrypted with the current "
            "CUFA_ENCRYPTION_KEY. If the key was rotated, reconnect Google."
        ) from exc


def _main(argv: list[str]) -> int:
    if len(argv) == 2 and argv[1] == "keygen":
        print(generate_key())
        return 0
    print("usage: python -m cufa.crypto keygen", file=sys.stderr)
    return 2


if __name__ == "__main__":  # pragma: no cover - tiny CLI shim
    raise SystemExit(_main(sys.argv))
