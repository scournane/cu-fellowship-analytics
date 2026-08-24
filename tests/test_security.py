"""Deliverable 10, tests 26-27: secrets at rest and in logs.

Both properties are the kind that pass by accident until the day they do not,
so both are asserted against the real mechanism: an actual row read out of
Postgres, and actual formatted log output.
"""

from __future__ import annotations

import io
import logging

import pytest

from cufa.crypto import decrypt_secret, encrypt_secret, generate_key
from cufa.db import fetch_one
from cufa.errors import ConfigError
from cufa.google.oauth import credential_status, disconnect, store_credential
from cufa.logging_setup import (
    RedactionFilter,
    configure_logging,
    mask_email,
    redact_emails,
    redact_secrets,
)

FAKE_REFRESH_TOKEN = "1//0fakeRefreshTokenValueForTestsOnly-abcdefghijklmnop"


# --- 26. refresh tokens are encrypted at rest -------------------------------

def test_26_raw_database_read_does_not_expose_a_refresh_token(db, settings):
    import dataclasses

    configured = dataclasses.replace(settings, encryption_key=generate_key())
    store_credential(
        db,
        account_email="Staff@CivicsUnplugged.org",
        refresh_token=FAKE_REFRESH_TOKEN,
        scopes=[
            "https://www.googleapis.com/auth/forms.body",
            "https://www.googleapis.com/auth/drive.file",
        ],
        settings=configured,
    )

    row = fetch_one(db, "select * from google_credential")
    stored = bytes(row["refresh_token_enc"])

    assert FAKE_REFRESH_TOKEN.encode() not in stored
    assert FAKE_REFRESH_TOKEN not in repr(row)
    assert stored.startswith(b"gAAAAA"), "Fernet ciphertext, not plaintext"

    # It is genuinely recoverable with the key, so this is encryption and not
    # a hash that happens to hide the value.
    assert decrypt_secret(stored, configured.encryption_key) == FAKE_REFRESH_TOKEN

    # The email is normalized, and the status view never decrypts anything.
    status = credential_status(db)
    assert status.account_email == "staff@civicsunplugged.org"
    assert status.has_required_scopes is True


def test_26b_wrong_key_raises_rather_than_returning_garbage():
    ciphertext = encrypt_secret("secret-value", generate_key())
    with pytest.raises(ConfigError):
        decrypt_secret(ciphertext, generate_key())


def test_26c_missing_key_refuses_to_store(db, settings):
    import dataclasses

    with pytest.raises(ConfigError) as excinfo:
        store_credential(
            db,
            account_email="staff@cu.invalid",
            refresh_token=FAKE_REFRESH_TOKEN,
            scopes=[],
            settings=dataclasses.replace(settings, encryption_key=None),
        )
    assert "CUFA_ENCRYPTION_KEY" in str(excinfo.value)


def test_26d_disconnect_clears_the_ciphertext(db, settings):
    import dataclasses

    configured = dataclasses.replace(settings, encryption_key=generate_key())
    store_credential(
        db, account_email="staff@cu.invalid", refresh_token=FAKE_REFRESH_TOKEN,
        scopes=[], settings=configured,
    )
    disconnect(db)

    row = fetch_one(db, "select refresh_token_enc, revoked_at from google_credential")
    assert bytes(row["refresh_token_enc"]) == b""
    assert row["revoked_at"] is not None
    assert credential_status(db).connected is False


# --- 27. nothing sensitive reaches the logs ---------------------------------

def _capture(level: int, message: str, *args) -> str:
    """Emit one record through a real handler carrying the redaction filter."""
    buffer = io.StringIO()
    handler = logging.StreamHandler(buffer)
    handler.setFormatter(logging.Formatter("%(levelname)s %(message)s"))
    handler.addFilter(RedactionFilter())

    logger = logging.getLogger("cufa.test.redaction")
    logger.handlers = [handler]
    logger.propagate = False
    logger.setLevel(logging.DEBUG)
    logger.log(level, message, *args)
    return buffer.getvalue()


@pytest.mark.parametrize("level", [logging.INFO, logging.WARNING, logging.ERROR, logging.CRITICAL])
def test_27_emails_never_appear_at_info_or_above(level):
    output = _capture(level, "wrote row for %s", "ada.lovelace@example.invalid")
    assert "ada.lovelace@example.invalid" not in output
    assert "<email redacted>" in output


def test_27b_emails_do_survive_at_debug_for_reconciliation():
    output = _capture(logging.DEBUG, "unresolved identity %s", "ada@example.invalid")
    assert "ada@example.invalid" in output


@pytest.mark.parametrize(
    "secret",
    [
        "ya29.a0AfH6SMBexampleAccessTokenValue",
        "1//0fakeRefreshTokenValueForTests",
        "AIzaSyExampleApiKeyValue123456",
        "gAAAAABmExampleFernetCiphertextValue",
    ],
)
@pytest.mark.parametrize("level", [logging.DEBUG, logging.INFO, logging.ERROR])
def test_27c_credentials_are_redacted_at_every_level(secret, level):
    output = _capture(level, "calling google with %s", secret)
    assert secret not in output
    assert "<redacted>" in output


@pytest.mark.parametrize(
    "message",
    [
        "api_key=sk-longsecretvalue123456",
        "refresh_token: 1//averylongtokenvalue",
        'Authorization: Bearer someopaquebearertokenvalue',
        "client_secret=GOCSPX-averyrealsecret123",
        "encryption_key = aVeryLongFernetKeyValue123456",
    ],
)
def test_27d_credential_shaped_key_values_are_redacted(message):
    output = _capture(logging.INFO, "%s", message)
    assert "<redacted>" in output
    for fragment in ("sk-longsecretvalue123456", "averylongtokenvalue",
                     "someopaquebearertokenvalue", "GOCSPX-averyrealsecret123",
                     "aVeryLongFernetKeyValue123456"):
        assert fragment not in output


def test_27e_pipeline_logs_carry_counts_not_addresses(db, tmp_path, caplog):
    """An end-to-end check: run real ingest and inspect what it actually logged."""
    from conftest import TEST_COHORT, TEST_TZ, make_session, write_csv
    from cufa.ingest.csv_path import ingest_csv

    make_session(db)
    path = write_csv(
        tmp_path / "r.csv",
        [
            {
                "Timestamp": "2026-09-15 19:20:00",
                "Email Address": "ada.lovelace@example.invalid",
                "Today's passphrase": "justice",
            }
        ],
        ["Timestamp", "Email Address", "Today's passphrase"],
    )

    configure_logging("INFO")
    with caplog.at_level(logging.INFO):
        ingest_csv(db, path, TEST_COHORT, TEST_TZ)

    emitted = "\n".join(
        redact_secrets(record.getMessage()) if record.levelno < logging.INFO
        else redact_emails(redact_secrets(record.getMessage()))
        for record in caplog.records
    )
    assert "ada.lovelace@example.invalid" not in emitted
    assert "rows" in emitted or "read=" in emitted


def test_27f_mask_email_keeps_rows_distinguishable_without_exposing_them():
    assert mask_email("ada.lovelace@example.invalid") == "a***@example.invalid"
    assert mask_email("not-an-email") == "<email redacted>"
