# =============================================================
# OWNER: BAINTY KAUR
# =============================================================
FROM python:3.10-slim

WORKDIR /app

# GDAL is needed for rasterio (reading COG files from MinIO)
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        gdal-bin \
        libgdal-dev \
        gcc \
        g++ \
        python3-dev && \
    rm -rf /var/lib/apt/lists/*

COPY services/thermal/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy shared config (read-only reference — do not edit shared/)
COPY shared/ ./shared/

# Copy only this service's code
COPY services/thermal/ ./

ENV PYTHONUNBUFFERED=1

# Expose Prometheus metrics port
EXPOSE 8092

CMD ["python", "main.py"]
