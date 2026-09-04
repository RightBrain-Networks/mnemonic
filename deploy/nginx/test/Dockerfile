FROM alpine:3.23

RUN apk add --no-cache nginx nginx-mod-http-brotli

STOPSIGNAL SIGQUIT

CMD ["nginx", "-g", "daemon off;"]
