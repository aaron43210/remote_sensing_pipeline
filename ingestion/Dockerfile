FROM python:3.11-slim

WORKDIR /app

# System dependencies for NetCDF, Zarr, geospatial
RUN apt-get update && apt-get install -y --no-install-recommends \
    libhdf5-dev libnetcdf-dev gcc git \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY main.py .

# Non-root user for security
RUN useradd -m appuser && chown -R appuser /app
USER appuser

CMD ["python", "main.py"]
