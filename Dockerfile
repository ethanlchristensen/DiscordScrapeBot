FROM python:3.13-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV POETRY_VIRTUALENVS_CREATE=false
ENV POETRY_NO_INTERACTION=1

# Install Poetry
RUN pip install --no-cache-dir -U pip poetry

# Copy dependency files first (better layer caching)
COPY pyproject.toml poetry.lock ./

# Install dependencies
RUN poetry install --no-root --only main

# Copy application code
COPY main.py .

CMD ["python", "main.py"]