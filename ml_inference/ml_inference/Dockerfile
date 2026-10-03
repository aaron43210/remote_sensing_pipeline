# =============================================================
# OWNER: AARON
# =============================================================
FROM python:3.10-slim

WORKDIR /app
RUN apt-get update && apt-get install -y gcc g++ python3-dev && rm -rf /var/lib/apt/lists/*

COPY services/ml_inference/requirements.txt .
RUN pip install --upgrade pip wheel "setuptools<70" && pip install --no-cache-dir -r requirements.txt

COPY shared/ /app/shared/
COPY services/ml_inference/ /app/

# Download pretrained model if not present
RUN python -c "from model import PhysicsGuidedLightweightNet; \
    m = PhysicsGuidedLightweightNet(); \
    import torch; torch.save(m.state_dict(), 'model_weights.pth')"

ENV PYTHONUNBUFFERED=1

CMD ["python", "main.py"]
