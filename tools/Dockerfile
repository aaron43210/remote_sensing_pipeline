# Operations image: storage init, model promotion, admin bootstrap, data upload.
FROM python:3.11-slim-bookworm
ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1 SERVICE_NAME=tools
WORKDIR /app
COPY constraints.txt /tmp/constraints.txt
COPY libs/hs_common /tmp/hs_common
RUN apt-get update && apt-get install -y --no-install-recommends build-essential libexpat1 \
    && pip install --prefer-binary -c /tmp/constraints.txt /tmp/hs_common "psycopg[binary]==3.2.3" "bcrypt==4.2.0" && rm -rf /tmp/hs_common \
    && apt-get purge -y build-essential && apt-get autoremove -y && rm -rf /var/lib/apt/lists/*
COPY scripts/ ./scripts/
COPY tools/init.sh ./init.sh
CMD ["bash", "init.sh"]
