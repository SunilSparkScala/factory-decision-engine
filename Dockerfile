# Factory Decision Engine — Cloud Run image
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY . .

# The synthetic factory database is gitignored, so build it into the image (deterministic, seeded)
RUN python scripts/generate_data.py

# Cloud Run provides $PORT; default to 8080 for local `docker run`
CMD ["sh", "-c", "exec uvicorn app.api:app --host 0.0.0.0 --port ${PORT:-8080}"]
