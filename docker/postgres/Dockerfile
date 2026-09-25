# syntax=docker/dockerfile:1
FROM postgis/postgis:17-3.5

ARG PGVECTOR_VERSION=0.8.6

# pgvector is compiled into the existing PostGIS image so one PostgreSQL
# instance supports both geographic and semantic indexes on the same volume.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        build-essential \
        ca-certificates \
        git \
        postgresql-server-dev-17 \
    && git clone --branch "v${PGVECTOR_VERSION}" --depth 1 \
        https://github.com/pgvector/pgvector.git /tmp/pgvector \
    && make -C /tmp/pgvector OPTFLAGS="" \
    && make -C /tmp/pgvector install \
    && rm -rf /tmp/pgvector \
    && apt-get purge -y --auto-remove \
        build-essential \
        git \
        postgresql-server-dev-17 \
    && rm -rf /var/lib/apt/lists/*
