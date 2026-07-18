#!/usr/bin/env python3
"""
TuyaCam RTSP Bridge
--------------------
Mantiene UNA sola sesión OAuth con la nube de Tuya (compartida por
todas las cámaras) y, por cada cámara configurada, pide un stream RTSP
NATIVO y lo remuxea hacia mediamtx en su propia ruta local
(rtsp://<host>:8554/<nombre-camara>).

Separación de responsabilidades:
  - GlobalConfig: todo lo que se comparte entre cámaras (credenciales,
    base URL, métricas, timeouts por defecto).
  - CameraConfig: lo específico de UNA cámara (nombre, device_id,
    ruta RTSP local, overrides opcionales).
  - TuyaSession: UNA instancia, compartida. El OAuth es por proyecto
    (client_id/secret), no por cámara.
  - FfmpegBridge: UNA instancia POR CÁMARA. Cada una mantiene su
    propio estado de backoff/circuit breaker de forma independiente
    — si una cámara falla, las demás no se ven afectadas.

Resiliencia (por cámara, de forma independiente):
  - Backoff exponencial (1, 2, 4, 8, 16, 30s cap).
  - Circuit breaker: tras `consecutive_error_threshold` errores
    seguidos, pausa `circuit_breaker_cooldown` segundos.
  - Watchdog de frames + health check activo (OPTIONS RTSP).
  - Clasificación de errores (401 / 404 / timeout / reset / sin
    frames) para logs y métricas.

Variables de entorno requeridas (NO hardcodear credenciales):
  TUYA_CLIENT_ID
  TUYA_CLIENT_SECRET
  TUYA_CAMERAS     JSON: [{"name": "entrada", "device_id": "xxx"}, ...]
Opcionales:
  TUYA_BASE_URL    (default: https://openapi.tuyaus.com)
  RTSP_HOST        (default: 127.0.0.1)
  RTSP_PORT        (default: 8554)
  METRICS_PORT     (default: 9101)

Compatibilidad: si TUYA_CAMERAS no está pero sí TUYA_DEVICE_ID (config
de una sola cámara de versiones anteriores), se sintetiza una lista de
una cámara automáticamente.
"""

import asyncio
import hashlib
import hmac
import json
import logging
import os
import re
import time
import urllib.parse
from dataclasses import dataclass, field
from typing import Dict, List, Optional

import aiohttp
from aiohttp import web

_LOGGER = logging.getLogger("tuya_stream_proxy")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")


def _log_event(event: str, **fields):
    """Log estructurado: el mensaje es JSON válido (fácil de grep/
    parsear), con el timestamp/nivel normal del logger delante."""
    _LOGGER.info(json.dumps({"event": event, **fields}, ensure_ascii=False))


# ---------------------------------------------------------------------
# Config: separada en dos niveles
# ---------------------------------------------------------------------
@dataclass
class GlobalConfig:
    """Todo lo que se comparte entre todas las cámaras."""
    base_url: str
    client_id: str
    client_secret: str
    metrics_port: int = 9101
    token_refresh_margin: int = 300        # renovar token 5 min antes de expirar
    token_cache_path: str = "/data/tuya_token_cache.json"
    healthcheck_interval: int = 15
    healthcheck_timeout: int = 5
    stats_log_interval: int = 30
    healthy_run_seconds: int = 60
    backoff_base: int = 1
    backoff_max: int = 30
    consecutive_error_threshold: int = 50
    circuit_breaker_cooldown: int = 600
    frame_timeout: int = 10                # default; cada cámara puede overridearlo

    @classmethod
    def from_env(cls) -> "GlobalConfig":
        return cls(
            base_url=os.environ.get("TUYA_BASE_URL", "https://openapi.tuyaus.com"),
            client_id=os.environ["TUYA_CLIENT_ID"],
            client_secret=os.environ["TUYA_CLIENT_SECRET"],
            metrics_port=int(os.environ.get("METRICS_PORT", "9101")),
        )


@dataclass
class CameraConfig:
    """Lo específico de UNA cámara."""
    name: str
    device_id: str
    rtsp_target: str
    frame_timeout: Optional[int] = None    # None = usa el default de GlobalConfig
    # 0 = stream principal (HD), 1 = sub-stream (SD). La resolución y
    # bitrate reales dependen del modelo/firmware de la cámara. Ver:
    # https://developer.tuya.com/cn/docs/iot/video-streaming/rtsp-stream-allocation
    stream_type: int = 0


def load_cameras_from_env(global_cfg: GlobalConfig) -> List[CameraConfig]:
    rtsp_host = os.environ.get("RTSP_HOST", "127.0.0.1")
    rtsp_port = os.environ.get("RTSP_PORT", "8554")

    raw = os.environ.get("TUYA_CAMERAS")
    if raw:
        try:
            entries = json.loads(raw)
        except json.JSONDecodeError as err:
            raise RuntimeError(f"TUYA_CAMERAS no es JSON válido: {err}")
    else:
        # Compatibilidad con la config de una sola cámara (< v3.1).
        device_id = os.environ.get("TUYA_DEVICE_ID")
        if not device_id:
            raise RuntimeError(
                "No hay TUYA_CAMERAS ni TUYA_DEVICE_ID configurado. "
                "Configura al menos una cámara."
            )
        name = os.environ.get("RTSP_PATH", "tuya_cam")
        entries = [{"name": name, "device_id": device_id}]

    if not entries:
        raise RuntimeError("La lista de cámaras (TUYA_CAMERAS) está vacía.")

    cameras = []
    seen_names = set()
    for entry in entries:
        name = entry["name"]
        if name in seen_names:
            raise RuntimeError(f"Nombre de cámara duplicado: '{name}'")
        seen_names.add(name)
        cameras.append(
            CameraConfig(
                name=name,
                device_id=entry["device_id"],
                rtsp_target=f"rtsp://{rtsp_host}:{rtsp_port}/{name}",
                stream_type=int(entry.get("stream_type", 0)),
            )
        )
    return cameras


def _sha256_hex(data: str) -> str:
    return hashlib.sha256(data.encode()).hexdigest()


def _sign(message: str, secret: str) -> str:
    return hmac.new(secret.encode(), message.encode(), hashlib.sha256).hexdigest().upper()


# Clasificación de errores para logs/métricas. La acción de
# recuperación real converge en "pedir URL nueva + reiniciar ffmpeg"
# para casi todos los casos, excepto "auth" que fuerza refresh de token.
_ERROR_PATTERNS = [
    ("auth", re.compile(r"401|unauthorized", re.I)),
    ("not_found", re.compile(r"404|not found", re.I)),
    ("timeout", re.compile(r"timed out|i/o timeout|connection timed out", re.I)),
    ("reset", re.compile(r"connection reset|broken pipe|econnreset", re.I)),
]


def _classify_error(text: str) -> str:
    for label, pattern in _ERROR_PATTERNS:
        if pattern.search(text):
            return label
    return "unknown"


# ---------------------------------------------------------------------
# Métricas Prometheus, con label `camera` por cámara
# ---------------------------------------------------------------------
class Metrics:
    def __init__(self):
        self.start_time = time.time()
        self.token_refreshes_total = 0
        self._cameras: Dict[str, dict] = {}

    def _cam(self, name: str) -> dict:
        return self._cameras.setdefault(name, {
            "frames_total": 0,
            "restarts_total": 0,
            "bitrate_kbps": 0.0,
            "stream_up": 0,
        })

    def add_frames(self, camera: str, delta: int):
        if delta > 0:
            self._cam(camera)["frames_total"] += delta

    def inc_restarts(self, camera: str):
        self._cam(camera)["restarts_total"] += 1

    def set_bitrate(self, camera: str, kbps: float):
        self._cam(camera)["bitrate_kbps"] = kbps

    def set_stream_up(self, camera: str, up: bool):
        self._cam(camera)["stream_up"] = 1 if up else 0

    def render(self) -> str:
        uptime = time.time() - self.start_time
        lines = [
            "# HELP tuya_uptime_seconds Segundos desde que arrancó el add-on",
            "# TYPE tuya_uptime_seconds gauge",
            f"tuya_uptime_seconds {uptime:.1f}",
            "# HELP tuya_token_refresh_total Veces que se renovó el token de Tuya (compartido)",
            "# TYPE tuya_token_refresh_total counter",
            f"tuya_token_refresh_total {self.token_refreshes_total}",
            "# HELP tuya_frames_total Frames procesados por cámara",
            "# TYPE tuya_frames_total counter",
        ]
        for name, cam in self._cameras.items():
            lines.append(f'tuya_frames_total{{camera="{name}"}} {cam["frames_total"]}')
        lines += [
            "# HELP tuya_restarts_total Reinicios del ciclo ffmpeg por cámara",
            "# TYPE tuya_restarts_total counter",
        ]
        for name, cam in self._cameras.items():
            lines.append(f'tuya_restarts_total{{camera="{name}"}} {cam["restarts_total"]}')
        lines += [
            "# HELP tuya_bitrate_kbps Bitrate actual reportado por ffmpeg, por cámara",
            "# TYPE tuya_bitrate_kbps gauge",
        ]
        for name, cam in self._cameras.items():
            lines.append(f'tuya_bitrate_kbps{{camera="{name}"}} {cam["bitrate_kbps"]:.1f}')
        lines += [
            "# HELP tuya_stream_up 1 si la cámara está publicando activamente, 0 si no",
            "# TYPE tuya_stream_up gauge",
        ]
        for name, cam in self._cameras.items():
            lines.append(f'tuya_stream_up{{camera="{name}"}} {cam["stream_up"]}')
        return "\n".join(lines) + "\n"


async def start_metrics_server(metrics: Metrics, port: int):
    async def handle_metrics(request):
        return web.Response(text=metrics.render(), content_type="text/plain")

    app = web.Application()
    app.router.add_get("/metrics", handle_metrics)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    _LOGGER.info("Métricas Prometheus en :%s/metrics", port)


# ---------------------------------------------------------------------
# Sesión Tuya: UNA sola, compartida por todas las cámaras
# ---------------------------------------------------------------------
class TuyaSession:
    def __init__(self, cfg: GlobalConfig, http: aiohttp.ClientSession, metrics: Metrics):
        self.cfg = cfg
        self.http = http
        self.metrics = metrics
        self._access_token: Optional[str] = None
        self._expires_at: float = 0
        self._lock = asyncio.Lock()
        self._had_token_before = False
        self._load_cached_token()

    def _load_cached_token(self):
        try:
            with open(self.cfg.token_cache_path) as f:
                data = json.load(f)
            if data.get("expires_at", 0) - self.cfg.token_refresh_margin > time.time():
                self._access_token = data["access_token"]
                self._expires_at = data["expires_at"]
                self._had_token_before = True
                _log_event("token_loaded_from_cache", expires_in=round(self._expires_at - time.time()))
        except FileNotFoundError:
            pass
        except Exception as err:
            _LOGGER.debug("No se pudo leer el caché de token: %s", err)

    def _save_cached_token(self):
        try:
            os.makedirs(os.path.dirname(self.cfg.token_cache_path), exist_ok=True)
            with open(self.cfg.token_cache_path, "w") as f:
                json.dump({"access_token": self._access_token, "expires_at": self._expires_at}, f)
        except Exception as err:
            _LOGGER.debug("No se pudo guardar el caché de token: %s", err)

    async def get_token(self) -> str:
        async with self._lock:
            if self._access_token and time.time() < self._expires_at - self.cfg.token_refresh_margin:
                return self._access_token
            await self._refresh_token()
            return self._access_token

    async def force_refresh_token(self) -> str:
        async with self._lock:
            await self._refresh_token()
            return self._access_token

    async def _refresh_token(self):
        start = time.time()
        t = str(int(time.time() * 1000))
        path = "/v1.0/token?grant_type=1"
        sign_str = self.cfg.client_id + t + "GET\n" + _sha256_hex("") + "\n\n" + path
        headers = {
            "client_id": self.cfg.client_id,
            "t": t,
            "sign_method": "HMAC-SHA256",
            "sign": _sign(sign_str, self.cfg.client_secret),
        }
        async with self.http.get(self.cfg.base_url + path, headers=headers, timeout=10) as resp:
            data = await resp.json()
        if not data.get("success"):
            raise RuntimeError(f"Tuya token error: {data}")
        self._access_token = data["result"]["access_token"]
        self._expires_at = time.time() + data["result"]["expire_time"]
        self._save_cached_token()
        self.metrics.token_refreshes_total += 1
        elapsed_ms = round((time.time() - start) * 1000)
        if self._had_token_before:
            _log_event("token_refreshed", expires_in=data["result"]["expire_time"], elapsed_ms=elapsed_ms)
        else:
            _log_event("token_obtained", expires_in=data["result"]["expire_time"], elapsed_ms=elapsed_ms)
            self._had_token_before = True

    async def allocate_stream(self, device_id: str, stream_type: int = 0) -> str:
        """Pide a Tuya una URL de stream RTSP nativa para UN device_id
        específico. La sesión (token) es compartida; device_id y
        stream_type son lo único que varía por cámara.

        stream_type: 0 = stream principal (HD), 1 = sub-stream (SD).
        """
        access_token = await self.get_token()
        body = json.dumps({"type": "RTSP", "stream_type": stream_type})
        t = str(int(time.time() * 1000))
        path = f"/v1.0/devices/{device_id}/stream/actions/allocate"
        sign_str = (
            self.cfg.client_id + access_token + t
            + "POST\n" + _sha256_hex(body) + "\n\n" + path
        )
        headers = {
            "client_id": self.cfg.client_id,
            "access_token": access_token,
            "t": t,
            "sign_method": "HMAC-SHA256",
            "sign": _sign(sign_str, self.cfg.client_secret),
            "Content-Type": "application/json",
        }
        async with self.http.post(
            self.cfg.base_url + path, headers=headers, data=body, timeout=10
        ) as resp:
            data = await resp.json()
        if not data.get("success"):
            raise RuntimeError(f"Tuya stream allocate error: {data}")
        return data["result"]["url"]


# ---------------------------------------------------------------------
# Puente ffmpeg: UNA instancia POR CÁMARA
# ---------------------------------------------------------------------
class FfmpegBridge:
    def __init__(self, global_cfg: GlobalConfig, camera: CameraConfig, tuya: TuyaSession, metrics: Metrics):
        self.cfg = global_cfg
        self.camera = camera
        self.tuya = tuya
        self.metrics = metrics
        self._proc: Optional[asyncio.subprocess.Process] = None
        self._auth_error = False
        self._last_frame_at: float = 0
        self._frame_count = 0
        self._consecutive_errors = 0

    @property
    def _frame_timeout(self) -> int:
        return self.camera.frame_timeout or self.cfg.frame_timeout

    async def run_forever(self):
        while True:
            try:
                url = await self.tuya.allocate_stream(self.camera.device_id, self.camera.stream_type)
                _log_event("new_stream_url", camera=self.camera.name, stream_type=self.camera.stream_type)
                await self._run_ffmpeg(url)
            except Exception as err:
                reason_class = _classify_error(str(err))
                self._consecutive_errors += 1
                self.metrics.inc_restarts(self.camera.name)
                _log_event(
                    "reconnect",
                    camera=self.camera.name,
                    reason=reason_class,
                    stage="allocate_or_token",
                    consecutive_errors=self._consecutive_errors,
                    detail=str(err)[:200],
                )
                if reason_class == "auth":
                    try:
                        await self.tuya.force_refresh_token()
                    except Exception as refresh_err:
                        _LOGGER.warning("[%s] No se pudo renovar el token: %s", self.camera.name, refresh_err)
            await self._sleep_backoff_or_breaker()

    async def _sleep_backoff_or_breaker(self):
        if self._consecutive_errors == 0:
            await asyncio.sleep(1)
            return
        if self._consecutive_errors >= self.cfg.consecutive_error_threshold:
            _log_event(
                "circuit_breaker_open",
                camera=self.camera.name,
                errors=self._consecutive_errors,
                cooldown_seconds=self.cfg.circuit_breaker_cooldown,
            )
            await asyncio.sleep(self.cfg.circuit_breaker_cooldown)
            self._consecutive_errors = 0
            return
        exponent = min(self._consecutive_errors - 1, 5)  # 1,2,4,8,16,30(cap)
        delay = min(self.cfg.backoff_base * (2 ** exponent), self.cfg.backoff_max)
        _LOGGER.info(
            "[%s] Backoff: reintentando en %ss (%s errores seguidos)",
            self.camera.name, delay, self._consecutive_errors,
        )
        await asyncio.sleep(delay)

    async def _run_ffmpeg(self, tuya_rtsp_url: str):
        cmd = [
            "ffmpeg",
            "-nostdin",
            "-loglevel", "warning",
            "-stats",
            "-fflags", "nobuffer",
            "-rtsp_transport", "tcp",
            "-timeout", "8000000",
            "-i", tuya_rtsp_url,
            "-c", "copy",
            "-f", "rtsp",
            "-rtsp_transport", "tcp",
            self.camera.rtsp_target,
        ]
        self._auth_error = False
        self._frame_count = 0
        self._last_frame_at = time.time()
        self._last_error_class = "unknown"
        run_started_at = time.time()

        self._proc = await asyncio.create_subprocess_exec(
            *cmd, stderr=asyncio.subprocess.PIPE
        )
        self.metrics.set_stream_up(self.camera.name, True)

        stderr_task = asyncio.create_task(self._watch_stderr(self._proc))
        watchdog_task = asyncio.create_task(self._watchdog())
        healthcheck_task = asyncio.create_task(self._healthcheck_loop())
        proc_wait_task = asyncio.create_task(self._proc.wait())

        done, pending = await asyncio.wait(
            {proc_wait_task, watchdog_task, healthcheck_task},
            return_when=asyncio.FIRST_COMPLETED,
        )

        if watchdog_task in done:
            reason_class = "no_frames"
        elif healthcheck_task in done:
            reason_class = "healthcheck"
        elif self._auth_error:
            reason_class = "auth"
        else:
            reason_class = self._last_error_class

        for t in pending:
            t.cancel()
        stderr_task.cancel()
        if self._proc.returncode is None:
            self._proc.terminate()
            try:
                await asyncio.wait_for(self._proc.wait(), timeout=3)
            except asyncio.TimeoutError:
                self._proc.kill()
                await self._proc.wait()

        self.metrics.set_stream_up(self.camera.name, False)
        self.metrics.inc_restarts(self.camera.name)
        elapsed = time.time() - run_started_at

        if elapsed >= self.cfg.healthy_run_seconds:
            if self._consecutive_errors:
                _log_event(
                    "recovered", camera=self.camera.name,
                    after_errors=self._consecutive_errors, run_seconds=round(elapsed, 1),
                )
            self._consecutive_errors = 0
        else:
            self._consecutive_errors += 1

        _log_event(
            "reconnect",
            camera=self.camera.name,
            reason=reason_class,
            stage="ffmpeg",
            run_seconds=round(elapsed, 1),
            frames=self._frame_count,
            consecutive_errors=self._consecutive_errors,
        )

        if reason_class == "auth":
            try:
                await self.tuya.force_refresh_token()
            except Exception as err:
                _LOGGER.warning("[%s] No se pudo renovar el token: %s", self.camera.name, err)

    async def _watch_stderr(self, proc: asyncio.subprocess.Process):
        last_stats_log = time.time()
        frames_at_last_log = 0
        last_reported_frame = 0
        try:
            async for raw_line in proc.stderr:
                line = raw_line.decode(errors="ignore").strip()
                if not line:
                    continue

                if "frame=" in line:
                    self._last_frame_at = time.time()
                    match = re.search(r"frame=\s*(\d+)", line)
                    if match:
                        self._frame_count = int(match.group(1))
                        delta = self._frame_count - last_reported_frame
                        if delta > 0:
                            self.metrics.add_frames(self.camera.name, delta)
                            last_reported_frame = self._frame_count

                    bitrate_match = re.search(r"bitrate=\s*([\d.]+)\s*kbits/s", line)
                    if bitrate_match:
                        self.metrics.set_bitrate(self.camera.name, float(bitrate_match.group(1)))

                    now = time.time()
                    if now - last_stats_log >= self.cfg.stats_log_interval:
                        elapsed = now - last_stats_log
                        delta_frames = self._frame_count - frames_at_last_log
                        fps = delta_frames / elapsed if elapsed > 0 else 0
                        _LOGGER.info(
                            "[%s] Stream activo: %.1f frames/s, %.0f kbps",
                            self.camera.name, fps, self.metrics._cam(self.camera.name)["bitrate_kbps"],
                        )
                        last_stats_log = now
                        frames_at_last_log = self._frame_count
                    continue

                reason_class = _classify_error(line)
                if reason_class == "auth":
                    self._auth_error = True
                    _LOGGER.warning("[%s] ffmpeg reportó un posible 401: %s", self.camera.name, line)
                elif reason_class != "unknown":
                    self._last_error_class = reason_class
                    _LOGGER.warning("[%s] ffmpeg [%s]: %s", self.camera.name, reason_class, line)
                else:
                    _LOGGER.debug("[%s] ffmpeg: %s", self.camera.name, line)
        except asyncio.CancelledError:
            pass

    async def _watchdog(self):
        while True:
            await asyncio.sleep(2)
            if time.time() - self._last_frame_at > self._frame_timeout:
                return

    async def _healthcheck_loop(self):
        while True:
            await asyncio.sleep(self.cfg.healthcheck_interval)
            if not await self._rtsp_options_check():
                return

    async def _rtsp_options_check(self) -> bool:
        try:
            parsed = urllib.parse.urlparse(self.camera.rtsp_target)
            host = parsed.hostname or "127.0.0.1"
            port = parsed.port or 554
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(host, port),
                timeout=self.cfg.healthcheck_timeout,
            )
            request = f"OPTIONS {self.camera.rtsp_target} RTSP/1.0\r\nCSeq: 1\r\n\r\n"
            writer.write(request.encode())
            await writer.drain()
            data = await asyncio.wait_for(
                reader.read(200), timeout=self.cfg.healthcheck_timeout
            )
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:
                pass
            return b"200" in data
        except Exception as err:
            _LOGGER.debug("[%s] Health check RTSP falló: %s", self.camera.name, err)
            return False

    def stop(self):
        if self._proc and self._proc.returncode is None:
            self._proc.terminate()


async def main():
    global_cfg = GlobalConfig.from_env()
    cameras = load_cameras_from_env(global_cfg)
    metrics = Metrics()

    _LOGGER.info("Cámaras configuradas: %s", ", ".join(c.name for c in cameras))

    async with aiohttp.ClientSession() as http:
        tuya = TuyaSession(global_cfg, http, metrics)
        bridges = [FfmpegBridge(global_cfg, cam, tuya, metrics) for cam in cameras]

        await asyncio.gather(
            start_metrics_server(metrics, global_cfg.metrics_port),
            *[bridge.run_forever() for bridge in bridges],
        )


if __name__ == "__main__":
    asyncio.run(main())
