FROM python:3.11-alpine

RUN apk add --no-cache bash

WORKDIR /app

# Copy requirement files and install basic python dependencies
COPY custom-backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the backend app and the Alembic migrations it applies on startup
COPY custom-backend/ .
COPY database/alembic.ini database/alembic.ini
COPY database/alembic/ database/alembic/
COPY database/init_db.py database/init_db.py

RUN chmod +x entrypoint.sh

EXPOSE 8000

ENTRYPOINT ["./entrypoint.sh"]
