# =============================================================
# OWNER: ANANTAHANARAYANAN
# =============================================================
# Build context is ./preprocessing (see docker-compose.yml), so this image is
# self-contained: no shared/ or common/ folder is needed.
FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Source only -- tests and docs stay out of the image.
COPY *.py ./
COPY thermal/ ./thermal/

# Non-root user, as in the ingestion image.
RUN useradd -m appuser && chown -R appuser /app
USER appuser

CMD ["python", "main.py"]
