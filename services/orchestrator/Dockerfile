FROM python:3.13-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY entrypoint.sh .

ENV PORT=8080

ENTRYPOINT ["./entrypoint.sh"]
