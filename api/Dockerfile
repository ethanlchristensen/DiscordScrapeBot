FROM python:3.11-slim

RUN useradd -u 8877 discordscrapebot

ARG DJANGO_SUPERUSER_USERNAME
ARG DJANGO_SUPERUSER_PASSWORD
ARG DJANGO_SUPERUSER_EMAIL

WORKDIR /api

COPY . .

RUN pip install --no-cache-dir poetry
RUN poetry config virtualenvs.create false

ENV POETRY_VIRTUALENVS_CREATE=false
ENV POETRY_VIRTUALENVS_IN_PROJECT=false

RUN poetry install --no-root --no-interaction --no-ansi

EXPOSE 8000

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

RUN mkdir -p /api/data && \
    chown -R discordscrapebot:discordscrapebot /api && \
    chmod 775 /api/data

RUN python manage.py collectstatic --noinput
RUN python manage.py migrate
RUN python manage.py createsuperuser --noinput

RUN chown -R discordscrapebot:discordscrapebot /api && \
    chmod 775 /api && \
    chmod 664 db.sqlite3

USER discordscrapebot

CMD ["python", "manage.py", "runserver", "0.0.0.0:8000"]