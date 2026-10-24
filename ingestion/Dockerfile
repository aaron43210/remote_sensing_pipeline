# Build context: repository root (needs libs/).
FROM python:3.11-slim-bookworm
ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1 SERVICE_NAME=ingestion
WORKDIR /app
COPY constraints.txt /tmp/constraints.txt
COPY libs/ /tmp/libs/
RUN apt-get update && apt-get install -y --no-install-recommends build-essential libexpat1 \
    && pip install --prefer-binary -c /tmp/constraints.txt "/tmp/libs/hs_common[geo]" /tmp/libs/hydra \
        netCDF4 zarr numcodecs s3fs prosail requests cryptography \
    && rm -rf /tmp/libs \
    && apt-get purge -y build-essential && apt-get autoremove -y && rm -rf /var/lib/apt/lists/*
COPY ingestion/*.py ./
RUN useradd -m -u 10001 app && mkdir -p /home/app/.cache && chown -R app /app /home/app
USER app
ENV NUMBA_CACHE_DIR=/tmp/numba
HEALTHCHECK --interval=30s --timeout=5s CMD python -c "import urllib.request;urllib.request.urlopen('http://localhost:9100/metrics',timeout=3)"
CMD ["python", "service.py"]
