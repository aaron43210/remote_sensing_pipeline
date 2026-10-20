# Build context: repository root. Serves hydra.onnx with ONNX Runtime: no
# PyTorch in this image (the model is pulled from object storage at start).
FROM python:3.11-slim-bookworm
ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1 SERVICE_NAME=ml_inference \
    NUMBA_CACHE_DIR=/tmp/numba OMP_NUM_THREADS=2
WORKDIR /app
COPY constraints.txt /tmp/constraints.txt
COPY libs/ /tmp/libs/
RUN apt-get update && apt-get install -y --no-install-recommends build-essential libexpat1 \
    && pip install --prefer-binary -c /tmp/constraints.txt "/tmp/libs/hs_common[geo]" /tmp/libs/hydra zarr numcodecs s3fs \
    && rm -rf /tmp/libs \
    && apt-get purge -y build-essential && apt-get autoremove -y && rm -rf /var/lib/apt/lists/*
COPY ml_inference/*.py ./
RUN useradd -m -u 10001 app && chown -R app /app
USER app
# warm the numba cache so the first job is not slow
RUN python -c "import numpy as np; from hydra.features.mineral import MineralFeatures; MineralFeatures().transform(np.random.rand(4,255))"
HEALTHCHECK --interval=30s --timeout=5s CMD python -c "import urllib.request;urllib.request.urlopen('http://localhost:9100/metrics',timeout=3)"
CMD ["python", "service.py"]
