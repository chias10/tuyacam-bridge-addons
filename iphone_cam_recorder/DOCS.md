# iPhone Cam Auto Recorder

Escucha en tiempo real (websocket de Home Assistant) el sensor que publica
la URL de la cámara del iPhone, p. ej. `sensor.anna_camera_stream`, con el
atributo `stream_url: http://192.168.100.78:8090/camera`. Cada vez que la IP
cambia, el add-on usa la nueva. En cuanto la URL emite video (ffprobe), graba
con ffmpeg en `/media/iphone_recordings/<name>/AAAAMMDD_HHMMSS.mkv`.

## Opciones

| Opción | Descripción |
|---|---|
| `devices[].name` | Carpeta de grabaciones (letras, números, `_`, `-`). |
| `devices[].entity` | Entidad con el atributo `stream_url`. |
| `devices[].fallback_host` | IP/hostname alternativo del iPhone (VPN) para grabar fuera de la LAN. |
| `scan_interval` | Segundos entre reintentos de probar la URL. |
| `segment_minutes` | Duración de cada archivo. |
| `retention_days` | Borrado automático (0 = nunca). |
| `notify_service` | Opcional, p. ej. `notify.mobile_app_mi_telefono`. |

## Grabar fuera de la LAN

El sensor reporta la IP WiFi del iPhone (192.168.x.x). Fuera de casa esa IP
no existe para el servidor y el iPhone en datos móviles no acepta conexiones
entrantes, así que el add-on **no puede** alcanzarlo sin un túnel. Solución:

1. Instala **Tailscale** en el iPhone y en Home Assistant (add-on Tailscale
   con *userspace networking desactivado*, para que los add-ons puedan
   enrutar hacia la red tailnet).
2. Pon la IP Tailscale del iPhone (100.x.y.z) o su nombre MagicDNS en
   `fallback_host`. Se conserva puerto y ruta de la URL original
   (`http://100.x.y.z:8090/camera`).
3. La app del iPhone debe seguir sirviendo video en ese puerto.

WireGuard u otra VPN funcionan igual. Con la VPN activa el add-on prueba
primero la URL del sensor y luego el fallback.
