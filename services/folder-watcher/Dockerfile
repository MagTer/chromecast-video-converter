FROM alpine:3.20

RUN apk add --no-cache inotify-tools bash curl

WORKDIR /app

COPY watch.sh ./watch.sh
RUN chmod +x ./watch.sh

ENTRYPOINT ["./watch.sh"]
