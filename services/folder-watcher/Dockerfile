FROM alpine:3.18

RUN apk add --no-cache inotify-tools bash curl

WORKDIR /app

COPY watch.sh ./watch.sh
RUN chmod +x ./watch.sh

ENTRYPOINT ["./watch.sh"]
