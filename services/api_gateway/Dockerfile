# =============================================================
# OWNER: AARON
# =============================================================
FROM python:3.10-slim
WORKDIR /app
RUN apt-get update && apt-get install -y gcc g++ python3-dev && rm -rf /var/lib/apt/lists/*
COPY services/api_gateway/requirements.txt .
RUN pip install --upgrade pip wheel "setuptools<70" && pip install --no-cache-dir -r requirements.txt
COPY shared/ /app/shared/
COPY services/api_gateway/ /app/
COPY services/mineral_analysis/ /app/services/mineral_analysis/
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
