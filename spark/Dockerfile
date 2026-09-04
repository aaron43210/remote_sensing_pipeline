# =============================================================
# OWNER: AARON
# =============================================================
FROM python:3.10-slim

WORKDIR /app

# Install Java for Spark + GDAL for rasterio/rio-cogeo
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        default-jre-headless \
        gdal-bin \
        libgdal-dev \
        gcc \
        g++ \
        python3-dev && \
    rm -rf /var/lib/apt/lists/*

# Set GDAL config for pip builds
ENV CPLUS_INCLUDE_PATH=/usr/include/gdal
ENV C_INCLUDE_PATH=/usr/include/gdal

COPY spark/requirements.txt .
RUN pip install --upgrade pip wheel "setuptools<70" && GDAL_VERSION=$(gdal-config --version) pip install --no-cache-dir -r requirements.txt

COPY shared/ /app/shared/
COPY spark/ /app/

ENV PYTHONUNBUFFERED=1

CMD ["python", "batch_processor.py"]
