#!/usr/bin/with-contenv bashio
# meari_launcher.sh — fuente "Meari (P2P)" para el TuyaCam Bridge.
# Cada cámara Meari corre el cliente P2P (qemu+Bionic en amd64, nativo en arm64)
# y hace PUSH del H.264 al MISMO mediamtx del bridge: rtsp://127.0.0.1:8554/<name>
# (mediamtx ya acepta cualquier path por 'all_others: source: publisher').
set -e

MEARI_JSON=$(jq -c '.meari_cameras // []' /data/options.json)
COUNT=$(echo "$MEARI_JSON" | jq 'length')
if [ "$COUNT" = "0" ]; then
  bashio::log.info "Meari: no hay cámaras configuradas, omitiendo."
  exit 0
fi
bashio::log.info "Meari: ${COUNT} cámara(s) configurada(s)."

# Desplegar Bionic (sysroot arm64 mínimo) una sola vez.
if [ ! -e /apex/com.android.runtime/bin/linker64 ]; then
  bashio::log.info "Meari: desplegando Bionic mínimo..."
  tar xzf /opt/meari/bionic_min.tgz -C / 2>/dev/null || true
fi
export LD_LIBRARY_PATH="/opt/meari/libs:/system/lib64:/apex/com.android.runtime/lib64/bionic"
export TZ=UTC

# En amd64 el binario arm64 corre bajo qemu-user; en aarch64 corre nativo.
if [ "$(uname -m)" = "x86_64" ]; then
  RUNNER="qemu-aarch64 /opt/meari/meari_client"
else
  RUNNER="/opt/meari/meari_client"
fi

RTSP_HOST="127.0.0.1"
RTSP_PORT="8554"

# Un loop por cámara (auto-reconexión), en paralelo.
echo "$MEARI_JSON" | jq -c '.[]' | while read -r cam; do
  NAME=$(echo "$cam"   | jq -r '.name')
  DID=$(echo "$cam"    | jq -r '.did')
  SUF=$(echo "$cam"    | jq -r '.suffix')
  HK=$(echo "$cam"     | jq -r '.hostkey')
  INIT=$(echo "$cam"   | jq -r '.initstring')
  LIC=$(echo "$cam"    | jq -r '.licenceid')
  STREAM=$(echo "$cam" | jq -r '.stream // 0')   # 0 = HD (main), 1 = SD (sub)
  (
    while true; do
      bashio::log.info "Meari[${NAME}]: conectando (stream=${STREAM})..."
      $RUNNER "$DID" "$SUF" "$HK" "$INIT" "$LIC" 0 "$STREAM" \
        | ffmpeg -hide_banner -loglevel warning -use_wallclock_as_timestamps 1 \
            -analyzeduration 5M -probesize 5M -f h264 -i - \
            -c copy -f rtsp -rtsp_transport tcp "rtsp://${RTSP_HOST}:${RTSP_PORT}/${NAME}"
      bashio::log.warning "Meari[${NAME}]: stream cayó; reintento en 3s..."
      sleep 3
    done
  ) &
done

wait
