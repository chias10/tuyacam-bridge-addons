# iPhone Cam Auto Recorder

Cuando un dispositivo (iPhone) se conecta a tu WiFi, el add-on encuentra su
IP actual **por MAC** (así no importa que cambie por DHCP), prueba la URL de
cámara con ffprobe y, en cuanto hay video, lo graba con ffmpeg.

## Configuración

| Opción | Descripción |
|---|---|
| `devices[].name` | Nombre (carpeta de grabaciones). Solo letras, números, `_` y `-`. |
| `devices[].mac` | MAC del iPhone **en esta red** (Ajustes > WiFi > (i) > Dirección Wi-Fi). |
| `devices[].stream_url` | URL de la cámara; usa `{ip}` como marcador, p. ej. `http://{ip}:8080/video` o `rtsp://{ip}:8554/live`. |
| `scan_interval` | Segundos entre escaneos ARP (default 15). |
| `segment_minutes` | Duración de cada archivo .mkv. |
| `retention_days` | Borra grabaciones más viejas (0 = nunca). |
| `recordings_dir` | Carpeta de salida (default `/media/iphone_recordings`). |
| `notify_service` | Opcional, p. ej. `notify.mobile_app_mi_telefono`. |
| `interface` | Opcional, interfaz de red para arp-scan (ej. `eth0`). |

## Notas importantes

- **Desactiva "Dirección Wi-Fi privada"** o déjala fija para esa red; iOS usa
  una MAC distinta por red y es la que debes poner aquí.
- El iPhone debe estar ejecutando una app que sirva video en esa URL
  (IP Camera Lite, Larix, etc.); el add-on solo graba cuando la URL emite video.
- El add-on usa `host_network` para ver la LAN por ARP. Un iPhone en reposo
  puede tardar en responder ARP; si no lo detecta, baja `scan_interval`.
- Si la IP cambia durante la grabación, se reinicia con la nueva IP.
- Grabaciones en `/media/iphone_recordings/<name>/AAAAMMDD_HHMMSS.mkv`.
