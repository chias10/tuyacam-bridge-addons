#!/usr/bin/with-contenv bashio
set -e

TUYA_BASE_URL=$(bashio::config 'tuya_base_url')
TUYA_CLIENT_ID=$(bashio::config 'tuya_client_id')
TUYA_CLIENT_SECRET=$(bashio::config 'tuya_client_secret')
TUYA_DEVICE_ID=$(bashio::config 'tuya_device_id')
RTSP_PATH=$(bashio::config 'rtsp_path')

if [ -z "$TUYA_CLIENT_ID" ] || [ -z "$TUYA_CLIENT_SECRET" ] || [ -z "$TUYA_DEVICE_ID" ]; then
    bashio::log.fatal "Faltan tuya_client_id / tuya_client_secret / tuya_device_id en la configuración del add-on"
    exit 1
fi

export TUYA_BASE_URL TUYA_CLIENT_ID TUYA_CLIENT_SECRET TUYA_DEVICE_ID
export RTSP_TARGET="rtsp://127.0.0.1:8554/${RTSP_PATH}"

sed "s/{{RTSP_PATH}}/${RTSP_PATH}/g" /opt/mediamtx.yml.tpl > /opt/mediamtx.yml

bashio::log.info "Iniciando mediamtx (rtsp://<host>:8554/${RTSP_PATH})..."
/usr/local/bin/mediamtx /opt/mediamtx.yml &
MTX_PID=$!

# Le da un momento a mediamtx para levantar el listener antes de que
# ffmpeg intente publicar contra él.
sleep 2

bashio::log.info "Iniciando tuya_stream_proxy..."
python3 /opt/tuya_stream_proxy.py &
PROXY_PID=$!

_term() {
    bashio::log.info "Deteniendo add-on..."
    kill -TERM "$MTX_PID" "$PROXY_PID" 2>/dev/null || true
}
trap _term SIGTERM SIGINT

wait -n "$MTX_PID" "$PROXY_PID"
EXIT_CODE=$?
_term
exit $EXIT_CODE
