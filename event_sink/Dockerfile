FROM python:3.11-slim-bookworm
ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1 SERVICE_NAME=event_sink
WORKDIR /app
COPY constraints.txt /tmp/constraints.txt
COPY libs/hs_common /tmp/hs_common
RUN apt-get update && apt-get install -y --no-install-recommends build-essential libexpat1 \
    && pip install --prefer-binary -c /tmp/constraints.txt /tmp/hs_common pyarrow && rm -rf /tmp/hs_common \
    && apt-get purge -y build-essential && apt-get autoremove -y && rm -rf /var/lib/apt/lists/*
COPY event_sink/service.py ./
RUN useradd -m -u 10001 app && chown -R app /app
USER app
CMD ["python", "service.py"]
