FROM node:24-alpine@sha256:06d7adf3e0ef61a13f1d28682726366f97a15aa1888c934d3ecec86c33918b53 AS dependencies
WORKDIR /app
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund

FROM node:24-alpine@sha256:06d7adf3e0ef61a13f1d28682726366f97a15aa1888c934d3ecec86c33918b53 AS builder
WORKDIR /app
COPY --from=dependencies /app/node_modules ./node_modules
COPY frontend/ .
RUN npm run build

FROM nginx:1.30-alpine@sha256:47928bf67eb62a1863cb08d9ecb52d3e704304ba614eaee17884204677702951 AS runner
# The pinned nginx base predates Alpine's CVE-2026-111497 fix.
RUN apk add --no-cache --upgrade 'brotli-libs>=1.2.1-r0'
ARG VERSION=dev
ARG REVISION=unknown
LABEL org.opencontainers.image.source="https://github.com/RightBrain-Networks/mnemonic" \
      org.opencontainers.image.version="$VERSION" \
      org.opencontainers.image.revision="$REVISION" \
      org.opencontainers.image.licenses="AGPL-3.0-only"
COPY frontend/nginx.conf /etc/nginx/nginx.conf
COPY deploy/nginx/snippets/ /etc/nginx/snippets/
COPY --from=builder /app/dist /usr/share/nginx/html
USER nginx
EXPOSE 3000
HEALTHCHECK --interval=15s --timeout=5s --start-period=10s --retries=3 CMD wget -q -O /dev/null http://127.0.0.1:3000/healthz
ENTRYPOINT ["nginx"]
CMD ["-g", "daemon off;"]
