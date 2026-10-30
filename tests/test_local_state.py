import sys
import time

import pytest

from ai_assistant.infrastructure.adapters.outbound.llm.chatgpt_plan.credentials import (
    CREDENTIALS_ENTROPY,
    CredentialRecord,
    CredentialStore,
    ModelAccessLog,
    load_or_create_host_id,
)
from ai_assistant.infrastructure.secret_store import ProtectedFile, default_protector

windows_only = pytest.mark.skipif(sys.platform != "win32", reason="DPAPI solo existe en Windows")


def record() -> CredentialRecord:
    return CredentialRecord(
        issuer="https://auth.openai.com", subject="user-123", client_id="oaiapp_x",
        ext_agent_host_id="urn:uuid:1", id_token="id-token-value",
        access_token="access-token-value", refresh_token="refresh-token-value",
        token_type="Bearer", expires_at=time.time() + 3600,
        scopes=["openid", "chatgpt.tokens.use.direct"],
    )


@windows_only
def test_credentials_are_encrypted_at_rest_and_round_trip(tmp_path):
    path = tmp_path / "credentials.dpapi"
    store = CredentialStore(ProtectedFile(path, default_protector(CREDENTIALS_ENTROPY)))
    store.save(record())
    raw = path.read_bytes()
    for secret in (b"access-token-value", b"refresh-token-value", b"id-token-value"):
        assert secret not in raw
    loaded = store.load()
    assert loaded.refresh_token == "refresh-token-value"
    assert loaded.plan_usage_granted
    assert not path.with_name(path.name + ".tmp").exists()


@windows_only
def test_other_entropy_cannot_decrypt(tmp_path):
    from ai_assistant.infrastructure.secret_store import SecretStoreCorrupted

    path = tmp_path / "credentials.dpapi"
    CredentialStore(ProtectedFile(path, default_protector(CREDENTIALS_ENTROPY))).save(record())
    with pytest.raises(SecretStoreCorrupted):
        CredentialStore(ProtectedFile(path, default_protector(b"other"))).load()


def test_record_repr_hides_tokens():
    text = repr(record())
    assert "access-token-value" not in text and "refresh-token-value" not in text


def test_host_id_is_stable_and_opaque(tmp_path):
    path = tmp_path / "host-id"
    first = load_or_create_host_id(path)
    assert first.startswith("urn:uuid:")
    assert load_or_create_host_id(path) == first


def test_model_access_log_is_per_client(tmp_path):
    log = ModelAccessLog(tmp_path / "verified-models.json")
    log.record_completed("client-a", "model-1")
    reloaded = ModelAccessLog(tmp_path / "verified-models.json")
    assert set(reloaded.verified("client-a")) == {"model-1"}
    assert reloaded.verified("client-b") == {}
