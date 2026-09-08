# Official minimal distribution: no OCR tools or extra parser plugins.
FROM apache/tika:4.0.0-1@sha256:a8b442501f601fb15015de974f9afe13dd242b0f14e49a2b144fadcb214a555b
COPY tika-config.json start.sh healthcheck.sh /opt/mnemonic-tika/
USER 35002:35002
ENTRYPOINT ["/bin/sh", "/opt/mnemonic-tika/start.sh"]
