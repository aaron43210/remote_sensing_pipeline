# =============================================================
# OWNER: BAINTY KAUR
# =============================================================

FROM python:3.11-slim

# -------------------------------------------------------------
# Environment settings
# -------------------------------------------------------------

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# -------------------------------------------------------------
# System dependencies required by Rasterio / GDAL / PROJ
# -------------------------------------------------------------

RUN apt-get update && apt-get install -y \
    gcc \
    g++ \
    libexpat1 \
    libexpat1-dev \
    libgdal-dev \
    libgeos-dev \
    libproj-dev \
    && rm -rf /var/lib/apt/lists/*

# -------------------------------------------------------------
# Working directory
# -------------------------------------------------------------

WORKDIR /app

# -------------------------------------------------------------
# Install ONLY thermal-service dependencies
# -------------------------------------------------------------

COPY services/thermal/requirements.txt ./requirements.txt

RUN python -m pip install --no-cache-dir --upgrade pip && \
    python -m pip install --no-cache-dir -r requirements.txt

# -------------------------------------------------------------
# Copy ONLY the thermal service
# -------------------------------------------------------------

COPY services/thermal/ ./

# -------------------------------------------------------------
# Start thermal processing service
# -------------------------------------------------------------

CMD ["python", "main.py"]