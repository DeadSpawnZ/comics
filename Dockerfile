FROM python:3.11-slim-bookworm AS builder

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /code

# Build dependencies + headers needed to compile mysqlclient and Pillow
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    pkg-config \
    default-libmysqlclient-dev \
    libjpeg-dev \
    zlib1g-dev \
    libpng-dev \
    libfreetype6-dev \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .

# Install into a temporary directory
RUN pip install --upgrade pip && \
    pip install --no-cache-dir --prefix=/install -r requirements.txt


# 🔽 Final image (lighter)
FROM python:3.11-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /code

# Only the runtime shared libraries (no -dev/headers or build tools)
RUN apt-get update && apt-get install -y --no-install-recommends \
    libmariadb3 \
    libjpeg62-turbo \
    zlib1g \
    libpng16-16 \
    libfreetype6 \
    gettext \
    && rm -rf /var/lib/apt/lists/*

# Copy only what is needed from the builder
COPY --from=builder /install /usr/local

RUN groupadd --system app && useradd --system --gid app --home-dir /code --no-create-home app \
    && chown app:app /code

COPY --chown=app:app . .

USER app

# Compile the .po translations into .mo files (gettext is also used by makemessages)
RUN python manage.py compilemessages \
    && python manage.py collectstatic --noinput

CMD ["python", "manage.py", "runserver", "0.0.0.0:8000"]