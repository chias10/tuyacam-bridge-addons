# Changelog

## 1.4.1
- El nombre del dispositivo acepta espacios (se convierten en `_` para la carpeta).

## 1.4.0
- Se elimina el soporte fuera de LAN (`fallback_host`/`fallback_url`); solo graba en la misma red.

## 1.3.0
- `fallback_url`: URL fija (VPN) que no depende del sensor.

## 1.2.0
- Graba solo tras el trigger `launch` (ventana de `attempt_seconds`); si no hay video, espera al siguiente launch.

## 1.1.0
- Ahora escucha el sensor de HA (`stream_url`) por websocket en vez de escanear ARP.
- `fallback_host` para grabar fuera de la LAN vía VPN/Tailscale.

## 1.0.0
- Primera versión: detección por MAC (ARP), URL con `{ip}`, verificación
  de video con ffprobe, grabación en segmentos y retención automática.
