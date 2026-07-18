# Changelog — TuyaCam RTSP Bridge

## 3.2.2
- **Confirmado en producción**: el fix del watchdog de 3.2.1 funciona
  — stream estable, sin reconexiones, logs de "Stream activo" cada 30s.
- Fix de bitrate: en modo `-c copy` el campo `bitrate=` de `-progress`
  casi siempre viene como `N/A` (ffmpeg no lleva esa cuenta al no
  recodificar), así que `tuya_bitrate_kbps` se quedaba fijo en 0.
  Ahora se calcula directamente a partir de `total_size` (bytes
  acumulados entre lecturas / tiempo transcurrido), que sí es confiable.

## 3.2.1
- **Fix crítico del watchdog**: usaba `ffmpeg -stats`, que escribe su
  progreso con retorno de carro (`\r`, sobreescribe la misma línea) en
  vez de salto de línea real. Nuestro parser línea-por-línea nunca
  detectaba esas actualizaciones, así que el watchdog interpretaba
  "sin frames" y reiniciaba el stream **siempre**, exactamente a los
  `frame_timeout` segundos, sin importar si el stream estaba sano.
  Reemplazado por `-progress pipe:1` (el mecanismo de ffmpeg pensado
  para monitoreo programático: `clave=valor` con salto de línea real
  garantizado). El conteo de frames, bitrate, y el resumen periódico
  de "frames/s" ahora vienen de `stdout` (`-progress`) en vez de
  `stderr` (`-stats`); `stderr` queda solo para warnings/errores reales.

## 3.2.0
- **Selección de perfil de stream por cámara** (`stream_type`): `0`
  para el stream principal (HD, default) o `1` para el sub-stream
  (SD) — útil para cámaras que solo necesitas para detección de
  movimiento, para ahorrar ancho de banda. Se envía como parámetro en
  el `allocate` RTSP a Tuya. La resolución/bitrate real depende del
  modelo y firmware de cada cámara.

## 3.1.0
- **Soporte multi-cámara.** La opción `cameras` reemplaza a
  `tuya_device_id`/`rtsp_path`: ahora es una lista de
  `{name, device_id}`, una por cámara Tuya.
- **Separación de config en dos niveles**: `GlobalConfig` (credenciales,
  base URL, métricas, timeouts por defecto — compartido) y
  `CameraConfig` (nombre, device_id, ruta RTSP — por cámara).
- **Una sola sesión OAuth compartida**, un `FfmpegBridge` independiente
  por cámara — cada una con su propio backoff/circuit breaker; si una
  cámara falla, las demás no se ven afectadas.
- **Métricas con label `camera`**: `tuya_frames_total{camera="..."}`,
  `tuya_restarts_total{camera="..."}`, `tuya_bitrate_kbps{camera="..."}`,
  `tuya_stream_up{camera="..."}` — graficables por separado.
- `mediamtx.yml` pasa a ser estático (usa el path especial
  `all_others`), ya no hace falta generarlo por template por cámara.
- Compatibilidad: si no hay `cameras` configurado pero sí
  `TUYA_DEVICE_ID` (configs de versiones anteriores), se sintetiza
  automáticamente una lista de una sola cámara.

## 3.0.0
- **Backoff exponencial**: los reintentos ya no son fijos, siguen la
  secuencia 1s → 2s → 4s → 8s → 16s → 30s (cap), y se resetean a 0
  cuando un ciclo corre "sano" por más de 60s.
- **Circuit breaker**: tras 50 errores seguidos, pausa 10 minutos
  antes de volver a intentar, para no bombardear a Tuya si está caída.
- **Métricas Prometheus** en `:9101/metrics`: `tuya_frames_total`,
  `tuya_restarts_total`, `tuya_token_refresh_total`,
  `tuya_uptime_seconds`, `tuya_bitrate_kbps`, `tuya_stream_up`.
- **Logs de eventos en JSON** embebido (ej. `{"event": "token_refreshed",
  "expires_in": 7200, "elapsed_ms": 210}`), más fácil de parsear que
  texto libre.
- **Detección de bitrate**: se parsea `bitrate=` de la salida de
  ffmpeg y se expone como métrica, para notar si Tuya baja la calidad.
- **Clasificación de errores** (401 / 404 / timeout / reset / sin
  frames) para logs y métricas. La acción de recuperación converge en
  "pedir URL nueva + reiniciar ffmpeg" para casi todos los casos,
  excepto 401 que además fuerza una renovación de token.
- **Token persistido en disco** (`/data/tuya_token_cache.json`): si el
  add-on se reinicia y el token cacheado sigue vigente, se reusa en
  vez de autenticar desde cero.

## 2.1.0
- **Reconexión robusta ante cortes de Tuya** (ej. si el RTSP se cae a
  las 12h): si ffmpeg reporta un posible 401, se fuerza la renovación
  del token antes de pedir la próxima URL — sin reiniciar el add-on.
- **Watchdog de frames**: si no llegan frames nuevos por más de 10s
  (configurable), se mata ffmpeg y se reinicia el ciclo automáticamente.
- **Health check activo**: cada 15s (configurable) se manda un OPTIONS
  RTSP al stream local para confirmar que sigue respondiendo; si no,
  se reinicia el ciclo.
- **Logs ampliados**: se distingue "token obtenido" (primera vez) de
  "token renovado", se loguea el motivo de cada reconexión, y se
  reporta un resumen de frames/s cada 30s mientras el stream está
  activo.

## 2.0.0
- **Cambio de arquitectura**: se descubrió (gracias a pruebas directas
  del usuario con curl/ffplay) que la API de Tuya sí soporta un stream
  **RTSP nativo** pidiendo `{"type": "RTSP"}` en vez de `{"type": "hls"}`
  en el endpoint `stream/actions/allocate`. El HLS resultó ser un
  preview corto (~12-16s) pensado para thumbnails, no para reproducción
  sostenida — de ahí todos los parches de refresh proactivo, timeouts,
  etc. de las versiones 1.0.x.
  Con RTSP nativo:
  - Ya no hace falta el refresh proactivo cada 8s (1.0.5).
  - Ya no hace falta quitar el audio (1.0.2): el RTSP negocia códecs
    vía SDP, que sí trae el "header global" que le faltaba al AAC-ADTS
    del HLS, así que ahora se copian video Y audio tal cual (`-c copy`).
  - El pipeline es mucho más simple y estable: ffmpeg simplemente
    remuxea RTSP-a-RTSP (Tuya -> mediamtx local), sin recodificar nada.

## 1.0.5
- Se descubrió que el HLS que entrega Tuya en cada `allocate` dura
  solo ~12-16s (parece pensado para preview, no para reproducción
  sostenida) y termina limpio, sin error de red que `-rw_timeout` o
  `-reconnect` puedan detectar. Se cambió la estrategia: en vez de
  reaccionar cuando ffmpeg se cuelga, ahora se refresca **de forma
  proactiva** — se corta ffmpeg y se pide una URL nueva cada 8s, antes
  de que el clip de Tuya se acabe solo. Esto causa un corte breve del
  RTSP cada ~8s (los clientes deberían reconectar solos) pero mantiene
  el stream sostenido en vez de morir después de 12-16s.

## 1.0.4
- Se quitó la bandera `-reconnect_at_eof` de ffmpeg. Causaba un loop
  infinito: cada segmento `.ts` del HLS de Tuya termina normalmente
  (EOF legítimo), pero esa bandera hacía que ffmpeg tratara ese EOF
  como un corte de conexión y re-descargara el mismo segmento sin
  avanzar nunca.

## 1.0.3
- Se agregó `-rw_timeout` y banderas de reconexión (`-reconnect`,
  `-reconnect_streamed`, `-reconnect_delay_max`) para que ffmpeg falle
  rápido si el HLS de Tuya se estanca, en vez de quedarse colgado
  indefinidamente sin que el watchdog de Python lo detecte.

## 1.0.2
- Se quitó el audio (`-an`). El HLS "en vivo" de Tuya viene segmentado
  y cada corte de segmento producía discontinuidades que confundían al
  decoder AAC (errores "channel element not allocated"), inundando el
  log. El proxy es sobre todo para video, así que se simplificó.

## 1.0.1
- Primer intento de resolver el error "AAC with no global headers is
  currently not supported" con el bitstream filter `aac_adtstoasc`.
  No fue suficiente (el muxer RTSP arma el SDP antes de que el filtro
  llegue a generar el header), reemplazado en 1.0.2 por recodificar
  audio y luego, definitivamente, por quitarlo.

## 1.0.0
- Primera versión: mediamtx + tuya_stream_proxy.py empaquetados como
  Home Assistant Add-on. Sesión única con Tuya (token + signInfo
  renovados automáticamente), republicado como RTSP local vía ffmpeg.
