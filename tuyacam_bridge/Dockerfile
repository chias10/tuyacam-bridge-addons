ARG BUILD_FROM=ghcr.io/home-assistant/base:3.24
FROM $BUILD_FROM

# --- Dependencias del sistema -----------------------------------------
RUN apk add --no-cache \
        python3 \
        py3-pip \
        ffmpeg \
        curl \
        tar \
        jq

# --- Dependencias Python ------------------------------------------------
RUN pip install --no-cache-dir --break-system-packages aiohttp

# --- mediamtx (según arquitectura del contenedor) -----------------------
ARG BUILD_ARCH
ENV MTX_VERSION="v1.19.2"
RUN set -e; \
    case "${BUILD_ARCH}" in \
      amd64)   MTX_ARCH="linux_amd64"  ;; \
      aarch64) MTX_ARCH="linux_arm64"  ;; \
      *) echo "Arquitectura no soportada: ${BUILD_ARCH}" && exit 1 ;; \
    esac; \
    curl -sL "https://github.com/bluenviron/mediamtx/releases/download/${MTX_VERSION}/mediamtx_${MTX_VERSION}_${MTX_ARCH}.tar.gz" -o /tmp/mediamtx.tar.gz; \
    tar xzf /tmp/mediamtx.tar.gz -C /usr/local/bin mediamtx; \
    rm /tmp/mediamtx.tar.gz; \
    chmod +x /usr/local/bin/mediamtx

# --- Archivos del add-on -------------------------------------------------
COPY tuya_stream_proxy.py /opt/tuya_stream_proxy.py
COPY mediamtx.yml /opt/mediamtx.yml
COPY run.sh /
RUN chmod a+x /run.sh

CMD [ "/run.sh" ]
