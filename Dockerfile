FROM python:3.14-slim

# Empêche Python de créer les fichiers .pyc
ENV PYTHONDONTWRITEBYTECODE=1

# Affiche directement les logs Python/Django dans Docker
ENV PYTHONUNBUFFERED=1

WORKDIR /app

# Dépendances système nécessaires à :
# - GeoDjango
# - GDAL
# - GEOS
# - PROJ
# - PostgreSQL / PostGIS
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    g++ \
    gdal-bin \
    libgdal-dev \
    libgeos-dev \
    libproj-dev \
    libpq-dev \
    postgresql-client \
    && rm -rf /var/lib/apt/lists/*

# Indique explicitement où sont les headers GDAL
ENV CPLUS_INCLUDE_PATH=/usr/include/gdal
ENV C_INCLUDE_PATH=/usr/include/gdal

# Copie d'abord requirements pour profiter du cache Docker
COPY requirements.txt /app/requirements.txt

RUN pip install --upgrade pip \
    && pip install --no-cache-dir -r requirements.txt

# Copie ensuite tout le projet
COPY . /app/

EXPOSE 8000

CMD ["python", "manage.py", "runserver", "0.0.0.0:8000"]