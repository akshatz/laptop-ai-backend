FROM python:3.11-alpine

# tesseract: OCR for uploaded pictures (file_text.ocr_image), English data only.
RUN apk add --no-cache bash tesseract-ocr tesseract-ocr-data-eng

WORKDIR /app

# Copy requirement files and install basic python dependencies
COPY laptop-backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the backend app and the Alembic migrations it applies on startup
COPY laptop-backend/ .
COPY database/alembic.ini database/alembic.ini
COPY database/alembic/ database/alembic/
COPY database/init_db.py database/init_db.py

RUN chmod +x entrypoint.sh

EXPOSE 8000

ENTRYPOINT ["./entrypoint.sh"]
