import time

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from jwt.algorithms import RSAAlgorithm

from ai_assistant.conversations.adapters.outbound.chatgpt_plan import oauth


@pytest.fixture(scope="session")
def signing_key():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


@pytest.fixture(scope="session")
def jwks(signing_key):
    jwk = RSAAlgorithm.to_jwk(signing_key.public_key(), as_dict=True)
    jwk.update({"kid": "test-key", "alg": "RS256", "use": "sig"})
    return {"keys": [jwk]}


@pytest.fixture
def make_id_token(signing_key):
    def make(client_id: str, nonce: str | None, sub: str = "user-123", **overrides) -> str:
        now = int(time.time())
        claims = {"iss": oauth.ISSUER, "aud": client_id, "sub": sub, "iat": now, "exp": now + 600}
        if nonce is not None:
            claims["nonce"] = nonce
        claims.update(overrides)
        return jwt.encode(claims, signing_key, algorithm="RS256", headers={"kid": "test-key"})
    return make
