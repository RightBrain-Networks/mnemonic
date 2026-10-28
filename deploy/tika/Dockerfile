# Official minimal distribution: no OCR tools or extra parser plugins.
FROM apache/tika:4.0.0-1@sha256:a8b442501f601fb15015de974f9afe13dd242b0f14e49a2b144fadcb214a555b
ARG VERSION=dev
ARG REVISION=unknown
LABEL org.opencontainers.image.source="https://github.com/RightBrain-Networks/mnemonic" \
      org.opencontainers.image.version="$VERSION" \
      org.opencontainers.image.revision="$REVISION" \
      org.opencontainers.image.licenses="AGPL-3.0-only"
COPY tika-config.json start.sh healthcheck.sh /opt/mnemonic-tika/
USER 35002:35002
ENTRYPOINT ["/bin/sh", "/opt/mnemonic-tika/start.sh"]
