FROM python:3.11-slim AS builder

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /code

# Dependencias de build + libs necesarias
RUN apt-get update && apt-get install -y \
    build-essential \
    pkg-config \
    default-libmysqlclient-dev \
    libjpeg-dev \
    zlib1g-dev \
    libpng-dev \
    libfreetype6-dev \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .

# Instalamos en un directorio temporal
RUN pip install --upgrade pip && \
    pip install --no-cache-dir --prefix=/install -r requirements.txt


# 🔽 Imagen final (más ligera)
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /code

# Solo libs necesarias en runtime (sin build tools)
RUN apt-get update && apt-get install -y \
    default-libmysqlclient-dev \
    libjpeg-dev \
    zlib1g-dev \
    libpng-dev \
    libfreetype6-dev \
    && rm -rf /var/lib/apt/lists/*

# Copiamos solo lo necesario del builder
COPY --from=builder /install /usr/local

COPY . .

CMD ["python", "manage.py", "runserver", "0.0.0.0:8000"]