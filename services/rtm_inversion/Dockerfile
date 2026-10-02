FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .

RUN pip install --no-cache-dir -r requirements.txt

COPY rtm_inference_service.py .
COPY rtm_api.py .

EXPOSE 8000

CMD ["python", "rtm_api.py"]
