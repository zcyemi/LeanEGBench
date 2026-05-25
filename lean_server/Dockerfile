FROM python:3.13-slim

ENV DEBIAN_FRONTEND=noninteractive \
    ELAN_HOME=/root/.elan \
    PATH=/root/.elan/bin:$PATH \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        bash \
        ca-certificates \
        curl \
        git \
        xz-utils \
        zstd \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY . /app

RUN python -m pip install --upgrade pip \
    && python -m pip install . \
    && curl https://raw.githubusercontent.com/leanprover/elan/master/elan-init.sh -sSf | bash -s -- -y --default-toolchain none \
    && sed -i 's/\r$//' /app/start_server.sh /app/docker-entrypoint.sh \
    && chmod +x /app/start_server.sh /app/docker-entrypoint.sh

EXPOSE 8578

ENTRYPOINT ["/app/docker-entrypoint.sh"]