# Changelog

## 1.2.0
- Graba solo tras el trigger `launch` (ventana de `attempt_seconds`); si no hay video, espera al siguiente launch.

## 1.1.0
- Ahora escucha el sensor de HA (`stream_url`) por websocket en vez de escanear ARP.
- `fallback_host` para grabar fuera de la LAN vía VPN/Tailscale.

## 1.0.0
- Primera versión: detección por MAC (ARP), URL con `{ip}`, verificación
  de video con ffprobe, grabación en segmentos y retención automática.
