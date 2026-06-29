FROM python:3.12-slim

WORKDIR /app

# libpq-dev + gcc needed to build psycopg2; pypdfium2 (casparser dep) ships its own binary
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq-dev \
    gcc \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8000

# Use shell form so $PORT env var is expanded at runtime
CMD uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}
