import os
from typing import AsyncGenerator
import hvac
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession

# 🔐 FETCH SECRETS FROM OPENBAO
# custom-backend authenticates via AppRole (role/secret id only, no long-lived
# token) and reads the KV v2 secret written by openbao/bootstrap.sh.
def _load_secrets_from_openbao() -> dict:
    client = hvac.Client(url=os.environ["OPENBAO_ADDR"])
    client.auth.approle.login(
        role_id=os.environ["OPENBAO_ROLE_ID"],
        secret_id=os.environ["OPENBAO_SECRET_ID"],
    )
    return client.secrets.kv.v2.read_secret_version(
        path="custom-backend", raise_on_deleted_version=True
    )["data"]["data"]

secrets = _load_secrets_from_openbao()

# 🚀 DATABASE CONNECTION POOL CONFIGURATION
# Connects seamlessly to the internal container DNS name defined in docker-compose.yml
DATABASE_URL = (
    f"postgresql+asyncpg://{secrets['postgres_user']}:{secrets['postgres_password']}"
    f"@postgres-db:5432/{secrets['postgres_db']}"
)

engine = create_async_engine(
    DATABASE_URL,
    pool_size=10,
    max_overflow=5,
    pool_pre_ping=True
)
AsyncSessionLocal = async_sessionmaker(bind=engine, expire_on_commit=False)

async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with AsyncSessionLocal() as session:
        try:
            yield session
        finally:
            await session.close()
