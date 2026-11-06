FROM ghcr.io/astral-sh/uv:0.13.7-python3.14-trixie-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PATH="/app/.venv/bin:$PATH"

WORKDIR /app

COPY pyproject.toml uv.lock ./

RUN uv sync \
    --frozen \
    --no-dev \
    --no-install-project

COPY alembic.ini ./
COPY alembic ./alembic
COPY app ./app

RUN uv sync \
    --frozen \
    --no-dev \
    && groupadd \
        --system \
        fantasy \
    && useradd \
        --system \
        --gid fantasy \
        --home-dir /app \
        --shell /usr/sbin/nologin \
        fantasy \
    && chown -R fantasy:fantasy /app

USER fantasy

EXPOSE 8000

HEALTHCHECK \
    --interval=30s \
    --timeout=5s \
    --start-period=20s \
    --retries=3 \
    CMD python -c \
        "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=3)"

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips=*"]
