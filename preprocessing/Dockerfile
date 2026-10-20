# Build context: repository root (needs libs/). The science modules are this
# folder's own; service.py / thermal_service.py wire them to the platform.
FROM python:3.11-slim-bookworm
ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1 SERVICE_NAME=preprocessing
WORKDIR /app
COPY constraints.txt /tmp/constraints.txt
COPY preprocessing/requirements.txt /tmp/requirements.txt
COPY libs/hs_common /tmp/hs_common
RUN apt-get update && apt-get install -y --no-install-recommends build-essential libexpat1 \
    && pip install --prefer-binary -c /tmp/constraints.txt -r /tmp/requirements.txt "/tmp/hs_common[geo]" && rm -rf /tmp/hs_common \
    && apt-get purge -y build-essential && apt-get autoremove -y && rm -rf /var/lib/apt/lists/*
COPY preprocessing/*.py ./
COPY preprocessing/thermal/ ./thermal/
RUN useradd -m -u 10001 app && chown -R app /app
USER app
HEALTHCHECK --interval=30s --timeout=5s CMD python -c "import urllib.request;urllib.request.urlopen('http://localhost:9100/metrics',timeout=3)"
CMD ["python", "service.py"]
