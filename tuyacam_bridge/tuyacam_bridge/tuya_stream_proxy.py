#!/usr/bin/env python3
"""
TuyaCam Bridge
--------------
Mantiene UNA sola sesión con la nube de Tuya, renueva el access_token
automáticamente (persistido en disco para sobrevivir reinicios del
add-on), pide un stream RTSP NATIVO (no HLS) y lo remuxea hacia
mediamtx.

Resiliencia:
  - Backoff exponencial entre reintentos (1, 2, 4, 8, 16, 30s cap).
  - Circuit breaker: tras `consecutive_error_threshold` errores
    seguidos, pausa `circuit_breaker_cooldown` segundos antes de
    reintentar, para no bombardear a Tuya si está caída.
  - Watchdog de frames: reinicia si no llegan frames nuevos por
    `frame_timeout` segundos.
  - Health check activo: OPTIONS RTSP periódico al stream local.
  - Clasificación de errores (401 / 404 / timeout / reset / sin
    frames) para logs y métricas más útiles — la acción de
    recuperación real converge en "pedir URL nueva + reiniciar
    ffmpeg" para casi todos los casos, EXCEPTO 401, que fuerza una
    renovación de token antes de reintentar.
  - Métricas Prometheus en :9101/metrics (frames, restarts, refreshes
    de token, uptime, bitrate).
  - Logs de eventos clave en JSON embebido (fácil de grep/parsear).

Home Assistant, Frigate, VLC y Alexa consumen todos el mismo RTSP local
(rtsp://<host>:8554/<camara>), sin abrir sesiones independientes contra
Tuya.

Variables de entorno requeridas (NO hardcodear credenciales):
  TUYA_CLIENT_ID
  TUYA_CLIENT_SECRET
  TUYA_DEVICE_ID
Opcionales:
  TUYA_BASE_URL   (default: https://openapi.tuyaus.com)
  RTSP_TARGET     (default: rtsp://127.0.0.1:8554/tuya_cam)
  METRICS_PORT    (default: 9101)
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
from dataclasses import dataclass
from typing import Optional

import aiohttp
from aiohttp import web

_LOGGER = logging.getLogger("tuya_stream_proxy")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")


def _log_event(event: str, **fields):
    """Log estructurado: el mensaje en sí es JSON válido, para que se
    pueda grepear/parsear fácil, pero sigue viéndose en el log de HA
    con su timestamp/nivel normal delante."""
    _LOGGER.info(json.dumps({"event": event, **fields}, ensure_ascii=False))


# ---------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------
@dataclass
class TuyaConfig:
    base_url: str
    client_id: str
    client_secret: str
    device_id: str
    rtsp_target: str
    metrics_port: int = 9101
    token_refresh_margin: int = 300      # renovar token 5 min antes de expirar
    token_cache_path: str = "/data/tuya_token_cache.json"
    frame_timeout: int = 10              # watchdog: reiniciar si no hay frames nuevos en N segundos
    healthcheck_interval: int = 15       # cada cuánto probar el RTSP local con OPTIONS
    healthcheck_timeout: int = 5         # timeout de esa prueba
    stats_log_interval: int = 30         # cada cuánto loguear frames/s
    healthy_run_seconds: int = 60        # a partir de cuánto tiempo corriendo se considera "sano" (resetea backoff)
    backoff_base: int = 1
    backoff_max: int = 30
    consecutive_error_threshold: int = 50  # circuit breaker: se abre tras N errores seguidos
    circuit_breaker_cooldown: int = 600    # 10 min

    @classmethod
    def from_env(cls) -> "TuyaConfig":
        return cls(
            base_url=os.environ.get("TUYA_BASE_URL", "https://openapi.tuyaus.com"),
            client_id=os.environ["TUYA_CLIENT_ID"],
            client_secret=os.environ["TUYA_CLIENT_SECRET"],
            device_id=os.environ["TUYA_DEVICE_ID"],
            rtsp_target=os.environ.get("RTSP_TARGET", "rtsp://127.0.0.1:8554/tuya_cam"),
            metrics_port=int(os.environ.get("METRICS_PORT", "9101")),
        )


def _sha256_hex(data: str) -> str:
    return hashlib.sha256(data.encode()).hexdigest()


def _sign(message: str, secret: str) -> str:
    return hmac.new(secret.encode(), message.encode(), hashlib.sha256).hexdigest().upper()


# Clasificación de errores: cada patrón mapea a una "reason_class" que
# se usa para logs/métricas. La acción de recuperación real es la
# misma para casi todas ("pedir URL nueva + reiniciar ffmpeg"),
# excepto "auth" que además fuerza una renovación de token.
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
# Métricas Prometheus
# ---------------------------------------------------------------------
class Metrics:
    def __init__(self):
        self.frames_total = 0
        self.restarts_total = 0
        self.token_refreshes_total = 0
        self.start_time = time.time()
        self.bitrate_kbps = 0.0
        self.stream_up = 0

    def render(self) -> str:
        uptime = time.time() - self.start_time
        lines = [
            "# HELP tuya_frames_total Frames procesados por ffmpeg desde que arrancó el add-on",
            "# TYPE tuya_frames_total counter",
            f"tuya_frames_total {self.frames_total}",
            "# HELP tuya_restarts_total Veces que se reinició el ciclo ffmpeg",
            "# TYPE tuya_restarts_total counter",
            f"tuya_restarts_total {self.restarts_total}",
            "# HELP tuya_token_refresh_total Veces que se renovó el token de Tuya",
            "# TYPE tuya_token_refresh_total counter",
            f"tuya_token_refresh_total {self.token_refreshes_total}",
            "# HELP tuya_uptime_seconds Segundos desde que arrancó el add-on",
            "# TYPE tuya_uptime_seconds gauge",
            f"tuya_uptime_seconds {uptime:.1f}",
            "# HELP tuya_bitrate_kbps Bitrate actual reportado por ffmpeg (kbps)",
            "# TYPE tuya_bitrate_kbps gauge",
            f"tuya_bitrate_kbps {self.bitrate_kbps:.1f}",
            "# HELP tuya_stream_up 1 si el stream está publicando activamente, 0 si no",
            "# TYPE tuya_stream_up gauge",
            f"tuya_stream_up {self.stream_up}",
        ]
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
# Sesión Tuya: un solo access_token compartido, persistido en disco
# ---------------------------------------------------------------------
class TuyaSession:
    def __init__(self, cfg: TuyaConfig, http: aiohttp.ClientSession, metrics: Metrics):
        self.cfg = cfg
        self.http = http
        self.metrics = metrics
        self._access_token: Optional[str] = None
        self._expires_at: float = 0
        self._lock = asyncio.Lock()
        self._had_token_before = False
        self._load_cached_token()

    def _load_cached_token(self):
        """Si el add-on se reinició pero el token cacheado en disco
        sigue vigente, lo reusamos en vez de autenticar desde cero."""
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
        """Fuerza una renovación aunque el token cacheado todavía
        parezca vigente (ej. tras detectar un 401 real de Tuya)."""
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

    async def allocate_stream(self) -> str:
        """Pide a Tuya una URL de stream RTSP nativa (no HLS)."""
        access_token = await self.get_token()
        body = json.dumps({"type": "RTSP"})
        t = str(int(time.time() * 1000))
        path = f"/v1.0/devices/{self.cfg.device_id}/stream/actions/allocate"
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
        _log_event("new_stream_url")
        return data["result"]["url"]


# ---------------------------------------------------------------------
# Puente ffmpeg: RTSP nativo de Tuya -> RTSP local (mediamtx)
# ---------------------------------------------------------------------
class FfmpegBridge:
    def __init__(self, cfg: TuyaConfig, tuya: TuyaSession, metrics: Metrics):
        self.cfg = cfg
        self.tuya = tuya
        self.metrics = metrics
        self._proc: Optional[asyncio.subprocess.Process] = None
        self._auth_error = False
        self._last_frame_at: float = 0
        self._frame_count = 0
        self._consecutive_errors = 0

    async def run_forever(self):
        while True:
            try:
                url = await self.tuya.allocate_stream()
                await self._run_ffmpeg(url)
            except Exception as err:
                reason_class = _classify_error(str(err))
                self._consecutive_errors += 1
                self.metrics.restarts_total += 1
                _log_event(
                    "reconnect",
                    reason=reason_class,
                    stage="allocate_or_token",
                    consecutive_errors=self._consecutive_errors,
                    detail=str(err)[:200],
                )
                if reason_class == "auth":
                    try:
                        await self.tuya.force_refresh_token()
                    except Exception as refresh_err:
                        _LOGGER.warning("No se pudo renovar el token: %s", refresh_err)
            await self._sleep_backoff_or_breaker()

    async def _sleep_backoff_or_breaker(self):
        if self._consecutive_errors == 0:
            await asyncio.sleep(1)  # pausa mínima entre ciclos, aunque venía sano
            return
        if self._consecutive_errors >= self.cfg.consecutive_error_threshold:
            _log_event(
                "circuit_breaker_open",
                errors=self._consecutive_errors,
                cooldown_seconds=self.cfg.circuit_breaker_cooldown,
            )
            await asyncio.sleep(self.cfg.circuit_breaker_cooldown)
            self._consecutive_errors = 0
            return
        exponent = min(self._consecutive_errors - 1, 5)  # 1,2,4,8,16,30(cap)
        delay = min(self.cfg.backoff_base * (2 ** exponent), self.cfg.backoff_max)
        _LOGGER.info("Backoff: reintentando en %ss (%s errores seguidos)", delay, self._consecutive_errors)
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
            self.cfg.rtsp_target,
        ]
        self._auth_error = False
        self._frame_count = 0
        self._last_frame_at = time.time()
        self._last_error_class = "unknown"
        run_started_at = time.time()

        self._proc = await asyncio.create_subprocess_exec(
            *cmd, stderr=asyncio.subprocess.PIPE
        )
        self.metrics.stream_up = 1

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

        self.metrics.stream_up = 0
        self.metrics.restarts_total += 1
        elapsed = time.time() - run_started_at

        if elapsed >= self.cfg.healthy_run_seconds:
            if self._consecutive_errors:
                _log_event("recovered", after_errors=self._consecutive_errors, run_seconds=round(elapsed, 1))
            self._consecutive_errors = 0
        else:
            self._consecutive_errors += 1

        _log_event(
            "reconnect",
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
                _LOGGER.warning("No se pudo renovar el token: %s", err)

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
                            self.metrics.frames_total += delta
                            last_reported_frame = self._frame_count

                    bitrate_match = re.search(r"bitrate=\s*([\d.]+)\s*kbits/s", line)
                    if bitrate_match:
                        self.metrics.bitrate_kbps = float(bitrate_match.group(1))

                    now = time.time()
                    if now - last_stats_log >= self.cfg.stats_log_interval:
                        elapsed = now - last_stats_log
                        delta_frames = self._frame_count - frames_at_last_log
                        fps = delta_frames / elapsed if elapsed > 0 else 0
                        _LOGGER.info(
                            "Stream activo: %.1f frames/s, %.0f kbps",
                            fps, self.metrics.bitrate_kbps,
                        )
                        last_stats_log = now
                        frames_at_last_log = self._frame_count
                    continue

                reason_class = _classify_error(line)
                if reason_class == "auth":
                    self._auth_error = True
                    _LOGGER.warning("ffmpeg reportó un posible 401: %s", line)
                elif reason_class != "unknown":
                    self._last_error_class = reason_class
                    _LOGGER.warning("ffmpeg [%s]: %s", reason_class, line)
                else:
                    _LOGGER.debug("ffmpeg: %s", line)
        except asyncio.CancelledError:
            pass

    async def _watchdog(self):
        while True:
            await asyncio.sleep(2)
            if time.time() - self._last_frame_at > self.cfg.frame_timeout:
                return

    async def _healthcheck_loop(self):
        while True:
            await asyncio.sleep(self.cfg.healthcheck_interval)
            if not await self._rtsp_options_check():
                return

    async def _rtsp_options_check(self) -> bool:
        try:
            parsed = urllib.parse.urlparse(self.cfg.rtsp_target)
            host = parsed.hostname or "127.0.0.1"
            port = parsed.port or 554
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(host, port),
                timeout=self.cfg.healthcheck_timeout,
            )
            request = f"OPTIONS {self.cfg.rtsp_target} RTSP/1.0\r\nCSeq: 1\r\n\r\n"
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
            _LOGGER.debug("Health check RTSP falló: %s", err)
            return False

    def stop(self):
        if self._proc and self._proc.returncode is None:
            self._proc.terminate()


async def main():
    cfg = TuyaConfig.from_env()
    metrics = Metrics()
    async with aiohttp.ClientSession() as http:
        tuya = TuyaSession(cfg, http, metrics)
        bridge = FfmpegBridge(cfg, tuya, metrics)
        await asyncio.gather(
            start_metrics_server(metrics, cfg.metrics_port),
            bridge.run_forever(),
        )


if __name__ == "__main__":
    asyncio.run(main())
