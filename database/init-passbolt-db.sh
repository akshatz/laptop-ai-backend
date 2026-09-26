#!/bin/sh
# Runs automatically by the official postgres image on first container start
# (via /docker-entrypoint-initdb.d/) — creates the separate database Passbolt
# needs, alongside the existing app_data database.
set -e

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<-EOSQL
    CREATE DATABASE passbolt;
    GRANT ALL PRIVILEGES ON DATABASE passbolt TO "$POSTGRES_USER";
EOSQL
