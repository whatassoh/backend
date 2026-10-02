FROM postgis/postgis:17-3.5

ARG PGVECTOR_VERSION=0.8.6

# Debian Bullseye a atteint sa fin de support.
# On fige les dépôts Debian sur un snapshot cohérent afin d'éviter
# les 404 actuels de bullseye-security.
#
# On conserve les autres sources APT (notamment PostgreSQL/PGDG)
# fournies par l'image de base.

RUN printf '%s\n' \
      'deb [check-valid-until=no] http://snapshot.debian.org/archive/debian/20260901T000000Z bullseye main' \
      'deb [check-valid-until=no] http://snapshot.debian.org/archive/debian/20260901T000000Z bullseye-updates main' \
      'deb [check-valid-until=no] http://snapshot.debian.org/archive/debian-security/20260901T000000Z bullseye-security main' \
      > /etc/apt/sources.list \
    && rm -f /etc/apt/sources.list.d/debian.sources \
    # PGDG no longer serves the archived Bullseye distribution from its live
    # repository.  Keep the base image's matching PostgreSQL packages, but
    # resolve them from its official archive so local Docker tests can build.
    && sed -i 's|http://apt\.postgresql\.org/pub/repos/apt|https://apt-archive.postgresql.org/pub/repos/apt|g' /etc/apt/sources.list.d/pgdg.list \
    && apt-get -o Acquire::Check-Valid-Until=false update \
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
