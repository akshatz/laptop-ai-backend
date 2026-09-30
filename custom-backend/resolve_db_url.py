"""
cRuns a command with INIT_DB_DATABASE_URL set to the Postgres connection string
built from OpenBao secrets, in the same way db.py does at import time. Used by
entrypoint.sh to run Alembic before the app starts, without duplicating the
AppRole-login/KV-read logic.

The URL contains the database password, so it's handed to the command only
through its environment and never printed:

    python resolve_db_url.py alembic -c database/alembic.ini upgrade head
"""
import os
import sys

import hvac


def resolve_db_url() -> str:
    client = hvac.Client(url=os.environ["OPENBAO_ADDR"])
    client.auth.approle.login(
        role_id=os.environ["OPENBAO_ROLE_ID"],
        secret_id=os.environ["OPENBAO_SECRET_ID"],
    )
    secrets = client.secrets.kv.v2.read_secret_version(
        path="custom-backend", raise_on_deleted_version=True
    )["data"]["data"]

    return (
        f"postgresql+asyncpg://{secrets['postgres_user']}:{secrets['postgres_password']}"
        f"@postgres-db:5432/{secrets['postgres_db']}"
    )


def main() -> None:
    command = sys.argv[1:]
    if not command:
        sys.exit("usage: python resolve_db_url.py <command> [args...]")
    env = {**os.environ, "INIT_DB_DATABASE_URL": resolve_db_url()}
    os.execvpe(command[0], command, env)


if __name__ == "__main__":
    main()
