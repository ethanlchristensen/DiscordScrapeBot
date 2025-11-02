FROM python:3.13-slim AS builder

RUN pip install poetry==2.2.0

ENV POETRY_NO_INTERACTION=1 \
    POETRY_VIRTUALENVS_IN_PROJECT=1 \
    POETRY_VIRTUALENVS_CREATE=1 \
    POETRY_CACHE_DIR=/tmp/poetry_cache

WORKDIR /app

COPY pyproject.toml poetry.lock* ./
RUN touch README.md
RUN poetry install --only=main --no-root && rm -rf $POETRY_CACHE_DIR


# --- Runtime stage ---
FROM python:3.13-slim AS runtime

ENV VIRTUAL_ENV=/app/.venv \
    PATH="/app/.venv/bin:$PATH"

WORKDIR /app

COPY --from=builder ${VIRTUAL_ENV} ${VIRTUAL_ENV}
COPY . .

CMD ["python", "bot.py"]