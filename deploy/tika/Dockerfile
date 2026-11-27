# Official minimal distribution: no OCR tools or extra parser plugins.
FROM apache/tika:4.2.0-2@sha256:0f0fbd7f053a2a9941b816ce6d6974493f5527f40c4d3491f9c2cecec30e612c
ARG VERSION=dev
ARG REVISION=unknown
LABEL org.opencontainers.image.source="https://github.com/RightBrain-Networks/mnemonic" \
      org.opencontainers.image.version="$VERSION" \
      org.opencontainers.image.revision="$REVISION" \
      org.opencontainers.image.licenses="AGPL-3.0-only"
# The Ubuntu base includes Pebble, but this image starts Java directly. Remove
# the unused supervisor and its separately embedded Go runtime from the image.
USER 0:0
RUN rm -f /usr/bin/pebble
COPY tika-config.json start.sh healthcheck.sh /opt/mnemonic-tika/
USER 35002:35002
ENTRYPOINT ["/bin/sh", "/opt/mnemonic-tika/start.sh"]
