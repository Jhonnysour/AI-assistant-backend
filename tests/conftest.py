import asyncio
import os
import time

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from jwt.algorithms import RSAAlgorithm

from ai_assistant.infrastructure.adapters.outbound.llm.chatgpt_plan import oauth


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


# --- PostgreSQL (SPEC-002) ---
# Las pruebas que lo necesitan se saltan si no hay AI_ASSISTANT_TEST_DATABASE_URL (ver docs/development.md).
# Usan una base de datos propia: se vacía antes de cada prueba.


@pytest.fixture(scope="session")
def postgres_url():
    url = os.environ.get("AI_ASSISTANT_TEST_DATABASE_URL")
    if not url:
        pytest.skip("Sin AI_ASSISTANT_TEST_DATABASE_URL: se omiten las pruebas con PostgreSQL.")
    from ai_assistant.infrastructure.adapters.outbound.persistence.postgres import schema
    schema.upgrade(url)
    return url


@pytest.fixture
def clean_postgres(postgres_url):
    from sqlalchemy import text
    from ai_assistant.infrastructure.adapters.outbound.persistence.postgres.repository import create_engine

    async def truncate():
        engine = create_engine(postgres_url)
        try:
            async with engine.begin() as connection:
                await connection.execute(text("TRUNCATE conversations CASCADE"))
        finally:
            await engine.dispose()

    asyncio.run(truncate())
    return postgres_url
