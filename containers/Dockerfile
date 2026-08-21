# syntax=docker/dockerfile:1.7
ARG PYTHON_IMAGE=python:3.12.4-slim-bookworm@sha256:a3e58f9399353be051735f09be0316bfdeab571a5c6a24fd78b92df85bcb2d85

FROM ${PYTHON_IMAGE} AS source
WORKDIR /src
COPY pyproject.toml README.md ./
COPY constraints/py312-direct.txt constraints/py312-direct.txt
COPY jaws ./jaws
COPY jaws_mcp ./jaws_mcp

FROM source AS analyzer-wheels
RUN python -m pip wheel --no-cache-dir --wheel-dir /wheels \
    --constraint constraints/py312-direct.txt ".[neo4j]"

FROM source AS mcp-wheels
RUN python -m pip wheel --no-cache-dir --wheel-dir /wheels \
    --constraint constraints/py312-direct.txt ".[neo4j,mcp]"

FROM source AS sensor-wheels
RUN python -m pip wheel --no-cache-dir --wheel-dir /wheels \
    --constraint constraints/py312-direct.txt ".[neo4j,capture]"

FROM ${PYTHON_IMAGE} AS runtime
ARG SOURCE_REVISION=unknown
ARG PACKAGE_VERSION=2.0.0
ARG BUILD_DATE=unknown
LABEL org.opencontainers.image.title="JAWS research workbench" \
      org.opencontainers.image.source="https://github.com/derekburgess/jaws" \
      org.opencontainers.image.revision="${SOURCE_REVISION}" \
      org.opencontainers.image.version="${PACKAGE_VERSION}" \
      org.opencontainers.image.created="${BUILD_DATE}" \
      org.opencontainers.image.licenses="GPL-2.0-only"
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    JAWS_ARTIFACT_ROOT=/var/lib/jaws/artifacts
RUN groupadd --gid 10001 jaws \
    && useradd --uid 10001 --gid jaws --create-home --shell /usr/sbin/nologin jaws \
    && install -d -o jaws -g jaws -m 0750 /var/lib/jaws/artifacts /var/lib/jaws/evidence
WORKDIR /workspace

FROM runtime AS analyzer
COPY --from=analyzer-wheels /wheels /wheels
RUN python -m pip install --no-cache-dir /wheels/* && rm -r /wheels
COPY --chown=10001:10001 benchmarks/v1 /workspace/benchmarks/v1
ENV JAWS_BENCHMARK_ROOT=/workspace/benchmarks/v1
USER 10001:10001
EXPOSE 8080
ENTRYPOINT ["jaws-runtime"]
CMD ["serve-health", "--role", "analyzer", "--host", "0.0.0.0", "--port", "8080"]

FROM runtime AS mcp
COPY --from=mcp-wheels /wheels /wheels
RUN python -m pip install --no-cache-dir /wheels/* && rm -r /wheels
USER 10001:10001
EXPOSE 8765
ENTRYPOINT ["jaws-mcp"]
CMD ["--host", "0.0.0.0", "--port", "8765"]

FROM runtime AS sensor
USER root
RUN apt-get update \
    && DEBIAN_FRONTEND=noninteractive apt-get install --yes --no-install-recommends tshark \
    && rm -rf /var/lib/apt/lists/*
COPY --from=sensor-wheels /wheels /wheels
RUN python -m pip install --no-cache-dir /wheels/* && rm -r /wheels
USER 10001:10001
ENTRYPOINT ["jaws-runtime"]
CMD ["serve-health", "--role", "sensor", "--host", "0.0.0.0", "--port", "8081"]
