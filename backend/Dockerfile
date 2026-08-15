FROM python:3.12-slim

WORKDIR /app
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1

COPY backend/requirements.txt /app/backend/requirements.txt
RUN pip install --no-cache-dir -r /app/backend/requirements.txt

COPY backend /app/backend
COPY contracts /app/contracts

EXPOSE 8000
# AWS creds come from env vars (docker run --env-file / task role / App Runner env)
CMD ["uvicorn", "backend.app:app", "--host", "0.0.0.0", "--port", "8000"]
