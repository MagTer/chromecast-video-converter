FROM python:3.12-slim-bookworm

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY alembic.ini .
COPY alembic ./alembic
COPY entrypoint.sh .

ENV PORT=8080

ENTRYPOINT ["./entrypoint.sh"]
