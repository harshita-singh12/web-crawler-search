# Build context is the repository root (see docker-compose.yml).
FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    libxml2 \
    libxslt1.1 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY indexer/requirements.txt ./indexer/requirements.txt
RUN pip install --no-cache-dir -r indexer/requirements.txt

COPY common ./common
COPY indexer/src ./indexer/src

ENV PYTHONUNBUFFERED=1

CMD ["python", "-m", "indexer.src.main"]
