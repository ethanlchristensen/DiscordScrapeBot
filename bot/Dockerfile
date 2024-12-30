FROM python:3.11-slim

WORKDIR /bot

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV POETRY_VIRTUALENVS_CREATE=false
ENV POETRY_VIRTUALENVS_IN_PROJECT=false

COPY . .

RUN pip install -U pip
RUN pip install --no-cache-dir poetry

RUN poetry install --no-root --no-interaction --no-ansi

CMD ["python", "main.py"]