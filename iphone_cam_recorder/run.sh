#!/usr/bin/with-contenv bashio
bashio::log.info "Iniciando iPhone Cam Auto Recorder..."
exec python3 -u /opt/recorder.py
