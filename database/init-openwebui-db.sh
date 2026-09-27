#!/bin/sh
# Runs automatically by the official postgres image on first container start
# (via /docker-entrypoint-initdb.d/) — creates the separate database Open
# WebUI needs, alongside the existing app_data/passbolt databases.
set -e

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<-EOSQL
    CREATE DATABASE open_webui;
    GRANT ALL PRIVILEGES ON DATABASE open_webui TO "$POSTGRES_USER";
EOSQL
