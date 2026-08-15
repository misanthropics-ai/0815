FROM python:3.12-slim

WORKDIR /app
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1

RUN addgroup --system app && adduser --system --ingroup app app

COPY backend/requirements.txt /app/backend/requirements.txt
RUN pip install --no-cache-dir -r /app/backend/requirements.txt

COPY --chown=app:app backend /app/backend
COPY --chown=app:app contracts /app/contracts
RUN mkdir -p /app/backend/data && chown -R app:app /app/backend/data

EXPOSE 8000
USER app

HEALTHCHECK --interval=30s --timeout=25s --start-period=60s --retries=3 \
  CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=20)"]

# AWS credentials come from the EC2/App Runner task role via the standard SDK chain.
CMD ["uvicorn", "backend.app:app", "--host", "0.0.0.0", "--port", "8000"]
