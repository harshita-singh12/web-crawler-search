# Build context is the repository root (see docker-compose.yml) so this
# image can COPY the shared `common/` package as well as its own source.
FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    libxml2 \
    libxslt1.1 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY crawler/requirements.txt ./crawler/requirements.txt
RUN pip install --no-cache-dir -r crawler/requirements.txt

COPY common ./common
COPY crawler/src ./crawler/src

ENV PYTHONUNBUFFERED=1

CMD ["python", "-m", "crawler.src.main"]
