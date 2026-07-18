#!/usr/bin/with-contenv bashio
set -e

TUYA_BASE_URL=$(bashio::config 'tuya_base_url')
TUYA_CLIENT_ID=$(bashio::config 'tuya_client_id')
TUYA_CLIENT_SECRET=$(bashio::config 'tuya_client_secret')

if [ -z "$TUYA_CLIENT_ID" ] || [ -z "$TUYA_CLIENT_SECRET" ]; then
    bashio::log.fatal "Faltan tuya_client_id / tuya_client_secret en la configuración del add-on"
    exit 1
fi

# La lista de cámaras es un objeto/array, así que se extrae directo de
# options.json con jq en vez de bashio::config (pensado para valores
# simples). Cada elemento: {"name": "...", "device_id": "..."}.
CAMERAS_JSON=$(jq -c '.cameras' /data/options.json)

if [ "$CAMERAS_JSON" = "null" ] || [ "$CAMERAS_JSON" = "[]" ]; then
    bashio::log.fatal "No hay ninguna cámara configurada en 'cameras'"
    exit 1
fi

CAMERA_COUNT=$(echo "$CAMERAS_JSON" | jq 'length')
bashio::log.info "Cámaras configuradas: ${CAMERA_COUNT}"

export TUYA_BASE_URL TUYA_CLIENT_ID TUYA_CLIENT_SECRET
export TUYA_CAMERAS="$CAMERAS_JSON"
export RTSP_HOST="127.0.0.1"
export RTSP_PORT="8554"

bashio::log.info "Iniciando mediamtx..."
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
