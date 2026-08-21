# Compatibility path for callers that still pass -f ocean/Dockerfile from the repository
# root. The supported GPU definition is containers/Dockerfile.gpu.
ARG CUDA_IMAGE=nvidia/cuda:12.8.1-runtime-ubuntu24.04@sha256:ebef3c171eeef0298e4eb2e4be843105edf3b8b0ac45e0b43acee358e8046867
FROM ${CUDA_IMAGE}
ARG SOURCE_REVISION=unknown
ARG PACKAGE_VERSION=2.0.0
ARG BUILD_DATE=unknown
LABEL org.opencontainers.image.title="JAWS deprecated GPU compatibility marker" \
      org.opencontainers.image.source="https://github.com/derekburgess/jaws" \
      org.opencontainers.image.revision="${SOURCE_REVISION}" \
      org.opencontainers.image.version="${PACKAGE_VERSION}" \
      org.opencontainers.image.created="${BUILD_DATE}" \
      org.opencontainers.image.licenses="GPL-2.0-only"
RUN printf '%s\n' 'Use containers/Dockerfile.gpu from the checked-out repository context.' \
    > /JAWS_GPU_IMAGE_MOVED
CMD ["/bin/sh", "-c", "cat /JAWS_GPU_IMAGE_MOVED; exit 64"]
