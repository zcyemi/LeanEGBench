FROM python:3.11-slim

ENV DEBIAN_FRONTEND=noninteractive \
    PIP_NO_CACHE_DIR=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        bash \
        build-essential \
        ca-certificates \
        curl \
        git \
        libgomp1 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY . /app

RUN sed -i 's/\r$//' /app/start_local_api.sh /app/docker-entrypoint.sh \
    && chmod +x /app/start_local_api.sh /app/docker-entrypoint.sh

EXPOSE 8580

ENTRYPOINT ["/app/docker-entrypoint.sh"]