# Compatibility path only. Prefer the pinned image reference in compose.dev.yml.
FROM neo4j:5.26.28-community-ubi10@sha256:f0a9509090d06027749f647314bae5a549f95192b4ac7080104226e448fd7793
ARG SOURCE_REVISION=unknown
ARG PACKAGE_VERSION=2.0.0
ARG BUILD_DATE=unknown
LABEL org.opencontainers.image.title="JAWS Neo4j compatibility image" \
      org.opencontainers.image.source="https://github.com/derekburgess/jaws" \
      org.opencontainers.image.revision="${SOURCE_REVISION}" \
      org.opencontainers.image.version="${PACKAGE_VERSION}" \
      org.opencontainers.image.created="${BUILD_DATE}" \
      org.opencontainers.image.licenses="GPL-2.0-only"
