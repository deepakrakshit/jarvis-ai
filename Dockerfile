# syntax=docker/dockerfile:1

FROM node:22.14.0-bookworm-slim AS node-runtime
FROM ghcr.io/astral-sh/uv:0.13.1 AS uv-runtime
FROM python:3.14.4-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/src \
    PATH=/app/.venv/bin:$PATH

WORKDIR /app

# Copy the pinned Node runtime and npm into the Python image so both halves of
# the application are available without a system-wide installation.
COPY --from=node-runtime /usr/local/bin/node /usr/local/bin/node
COPY --from=node-runtime /usr/local/bin/npm /usr/local/bin/npm
COPY --from=node-runtime /usr/local/bin/npx /usr/local/bin/npx
COPY --from=node-runtime /usr/local/lib/node_modules /usr/local/lib/node_modules
COPY --from=uv-runtime /uv /uvx /bin/

RUN apt-get update \
    && apt-get install --no-install-recommends --yes \
        build-essential \
        ca-certificates \
        git \
        libasound2 \
        libatk-bridge2.0-0 \
        libatk1.0-0 \
        libatspi2.0-0 \
        libcairo2 \
        libcups2 \
        libdbus-1-3 \
        libdrm2 \
        libgbm1 \
        libglib2.0-0 \
        libnspr4 \
        libnss3 \
        libpango-1.0-0 \
        libportaudio2 \
        libsndfile1 \
        libx11-6 \
        libx11-xcb1 \
        libxcb1 \
        libxcomposite1 \
        libxdamage1 \
        libxext6 \
        libxfixes3 \
        libxkbcommon0 \
        libxrandr2 \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml uv.lock requirements.lock ./
COPY substrate/package.json substrate/package-lock.json ./substrate/

RUN uv sync --locked --all-extras --no-install-project \
    && npm --prefix substrate ci --ignore-scripts

COPY . .

RUN uv sync --locked --all-extras \
    && python -m compileall --quiet src \
    && python -m playwright install chromium

EXPOSE 8765 8080

CMD ["python", "-m", "jarvis.cli", "serve", "--host", "0.0.0.0", "--no-browser"]
