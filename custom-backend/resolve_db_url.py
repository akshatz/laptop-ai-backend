"""
Prints the Postgres connection string built from OpenBao secrets, in the
same way db.py does at import time. Used by entrypoint.sh to populate
INIT_DB_DATABASE_URL for Alembic before the app starts, without duplicating
the AppRole-login/KV-read logic.
"""
import os
import hvac


def main() -> None:
    client = hvac.Client(url=os.environ["OPENBAO_ADDR"])
    client.auth.approle.login(
        role_id=os.environ["OPENBAO_ROLE_ID"],
        secret_id=os.environ["OPENBAO_SECRET_ID"],
    )
    secrets = client.secrets.kv.v2.read_secret_version(
        path="custom-backend", raise_on_deleted_version=True
    )["data"]["data"]

    print(
        f"postgresql+asyncpg://{secrets['postgres_user']}:{secrets['postgres_password']}"
        f"@postgres-db:5432/{secrets['postgres_db']}"
    )


if __name__ == "__main__":
    main()
