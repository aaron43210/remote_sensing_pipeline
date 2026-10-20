# Batch pipelines (Dagster): webserver + daemon in one container.
FROM python:3.11-slim-bookworm
ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1 DAGSTER_HOME=/opt/dagster/home
RUN apt-get update && apt-get install -y --no-install-recommends postgresql-client && rm -rf /var/lib/apt/lists/*
WORKDIR /opt/dagster/app
COPY constraints.txt /tmp/constraints.txt
COPY libs/hs_common /tmp/hs_common
RUN apt-get update && apt-get install -y --no-install-recommends build-essential libexpat1 \
    && pip install --prefer-binary -c /tmp/constraints.txt "/tmp/hs_common[geo]" "psycopg[binary]==3.2.3" pyarrow "pandas==2.2.3" \
      dagster==1.9.6 dagster-webserver==1.9.6 dagster-postgres==0.25.6 && rm -rf /tmp/hs_common \
    && apt-get purge -y build-essential && apt-get autoremove -y && rm -rf /var/lib/apt/lists/*
COPY pipelines/hydranet_pipelines ./hydranet_pipelines
COPY pipelines/dagster.yaml /opt/dagster/home/dagster.yaml
COPY pipelines/workspace.yaml ./workspace.yaml
EXPOSE 3000
CMD ["bash", "-c", "dagster-daemon run & exec dagster-webserver -h 0.0.0.0 -p 3000 -w workspace.yaml"]
