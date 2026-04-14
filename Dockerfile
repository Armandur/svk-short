FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends \
      sqlite3 curl && rm -rf /var/lib/apt/lists/*
RUN useradd --create-home --shell /bin/bash appuser
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app/ ./app/
RUN mkdir -p data && chown -R appuser:appuser /app
USER appuser
HEALTHCHECK --interval=30s --timeout=3s \
    CMD curl -fsS http://localhost:8000/healthz || exit 1
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
