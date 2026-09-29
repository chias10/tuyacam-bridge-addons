# iPhone Cam Auto Recorder

Escucha en tiempo real (websocket de Home Assistant) el sensor que publica
la URL de la cámara del iPhone, p. ej. `sensor.anna_camera_stream`, con el
atributo `stream_url: http://192.168.100.78:8090/camera`. Cada vez que la IP
cambia, el add-on usa la nueva. **Solo intenta grabar cuando el trigger cambia a `launch`** (app abierta): durante 15 s prueba la URL con ffprobe cada 2 s; si hay video graba con ffmpeg en `/media/iphone_recordings/<name>/AAAAMMDD_HHMMSS.mkv`.

## Opciones

| Opción | Descripción |
|---|---|
| `devices[].name` | Carpeta de grabaciones (los espacios y símbolos se convierten en `_`). |
| `devices[].entity` | Entidad con el atributo `stream_url`. |
| `devices[].trigger_entity` | Sensor de la app Companion que cambia al abrirla (default `sensor.anna_last_update_trigger`; verifica el ID exacto en HA). |
| `devices[].trigger_state` | Valor que dispara el intento (default `launch`). |
| `attempt_seconds` | Segundos que intenta obtener video tras cada launch (15). Si no hay, espera al siguiente launch. |
| `transcode_h264` | El stream de la app es MJPEG (pesado, ~GB/hora). `true` lo convierte a H.264 (~10x menos espacio) a costa de CPU. Default `false` (copia directa). |
| `segment_minutes` | Duración de cada archivo. |
| `retention_days` | Borrado automático (0 = nunca). |
| `notify_service` | Opcional, p. ej. `notify.mobile_app_mi_telefono`. |

## Alcance

Solo graba con el iPhone conectado a la misma LAN que Home Assistant
(la URL del sensor es una IP local). Fuera de la LAN no se intenta.

## Permisos

Un add-on no puede pedir permisos en el iPhone: solo intenta conectarse a
la URL. Los permisos (cámara/red local) los gestiona la app que sirve el
video; concédelos una vez en iOS. Si no hay video en `attempt_seconds`, el
add-on se rinde hasta el siguiente launch.
