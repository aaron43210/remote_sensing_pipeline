# =============================================================
# OWNER: ANANTHAN S & HARIKRISHNAN
# =============================================================
FROM python:3.10-slim

WORKDIR /app
RUN apt-get update && apt-get install -y gcc g++ python3-dev && rm -rf /var/lib/apt/lists/*

COPY services/mineral_analysis/requirements.txt .
RUN pip install --upgrade pip wheel "setuptools<70" && pip install --no-cache-dir -r requirements.txt

COPY shared/ /app/shared/
COPY services/mineral_analysis/ /app/

ENV PYTHONUNBUFFERED=1

CMD ["python", "main.py"]
