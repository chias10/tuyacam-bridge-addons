#!/usr/bin/with-contenv bashio
set -e

TUYA_BASE_URL=$(bashio::config 'tuya_base_url')
TUYA_CLIENT_ID=$(bashio::config 'tuya_client_id')
TUYA_CLIENT_SECRET=$(bashio::config 'tuya_client_secret')

# Listas de cámaras (objetos/arrays -> se leen con jq de options.json)
CAMERAS_JSON=$(jq -c '.cameras // []' /data/options.json)
MEARI_JSON=$(jq -c '.meari_cameras // []' /data/options.json)
TUYA_COUNT=$(echo "$CAMERAS_JSON" | jq 'length')
MEARI_COUNT=$(echo "$MEARI_JSON" | jq 'length')

if [ "$TUYA_COUNT" = "0" ] && [ "$MEARI_COUNT" = "0" ]; then
    bashio::log.fatal "No hay cámaras configuradas en 'cameras' ni 'meari_cameras'"
    exit 1
fi

if [ "$TUYA_COUNT" != "0" ] && { [ -z "$TUYA_CLIENT_ID" ] || [ -z "$TUYA_CLIENT_SECRET" ]; }; then
    bashio::log.fatal "Hay cámaras Tuya pero faltan tuya_client_id / tuya_client_secret"
    exit 1
fi

bashio::log.info "Cámaras -> Tuya: ${TUYA_COUNT} | Meari: ${MEARI_COUNT}"

export TUYA_BASE_URL TUYA_CLIENT_ID TUYA_CLIENT_SECRET
export TUYA_CAMERAS="$CAMERAS_JSON"
export RTSP_HOST="127.0.0.1"
export RTSP_PORT="8554"

bashio::log.info "Iniciando mediamtx..."
/usr/local/bin/mediamtx /opt/mediamtx.yml &
MTX_PID=$!
sleep 2   # deja que mediamtx levante el listener antes de publicar

PIDS="$MTX_PID"

# --- Fuente Tuya (solo si hay cámaras Tuya) ---
if [ "$TUYA_COUNT" != "0" ]; then
    bashio::log.info "Iniciando tuya_stream_proxy..."
    python3 /opt/tuya_stream_proxy.py &
    PIDS="$PIDS $!"
fi

# --- Fuente Meari/P2P (solo si hay cámaras Meari) ---
if [ "$MEARI_COUNT" != "0" ]; then
    bashio::log.info "Iniciando meari_launcher..."
    /opt/meari_launcher.sh &
    PIDS="$PIDS $!"
fi

_term() {
    bashio::log.info "Deteniendo add-on..."
    kill -TERM $PIDS 2>/dev/null || true
}
trap _term SIGTERM SIGINT

# Si cualquiera de los procesos muere, se cierra el add-on (HA lo reinicia).
wait -n $PIDS
EXIT_CODE=$?
_term
exit $EXIT_CODE
