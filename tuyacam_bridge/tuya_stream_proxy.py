#!/usr/bin/env python3
"""
Tuya Stream Proxy
------------------
Mantiene UNA sola sesión con la nube de Tuya, renueva el access_token
automáticamente antes de que expire, y pide un stream RTSP NATIVO
(no HLS) que republica hacia mediamtx.

Home Assistant, Frigate, VLC y Alexa consumen todos el mismo RTSP local
(rtsp://<host>:8554/<camara>), sin abrir sesiones independientes contra
Tuya. Esto resuelve el problema de "múltiples visualizaciones" porque
Tuya solo ve UN cliente conectado (este proxy), sin importar cuántos
consumidores locales haya.

Requisitos:
  pip install aiohttp
  ffmpeg instalado y en PATH
  mediamtx corriendo y escuchando en rtsp://127.0.0.1:8554

Variables de entorno requeridas (NO hardcodear credenciales):
  TUYA_CLIENT_ID
  TUYA_CLIENT_SECRET
  TUYA_DEVICE_ID
Opcionales:
  TUYA_BASE_URL      (default: https://openapi.tuyaus.com)
  RTSP_TARGET         (default: rtsp://127.0.0.1:8554/tuya_cam)
"""

import asyncio
import hashlib
import hmac
import json
import logging
import os
import time
from dataclasses import dataclass
from typing import Optional

import aiohttp

_LOGGER = logging.getLogger("tuya_stream_proxy")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")


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
    token_refresh_margin: int = 300      # renovar token 5 min antes de expirar
    ffmpeg_restart_backoff: int = 5      # espera entre reintentos si ffmpeg muere

    @classmethod
    def from_env(cls) -> "TuyaConfig":
        return cls(
            base_url=os.environ.get("TUYA_BASE_URL", "https://openapi.tuyaus.com"),
            client_id=os.environ["TUYA_CLIENT_ID"],
            client_secret=os.environ["TUYA_CLIENT_SECRET"],
            device_id=os.environ["TUYA_DEVICE_ID"],
            rtsp_target=os.environ.get("RTSP_TARGET", "rtsp://127.0.0.1:8554/tuya_cam"),
        )


def _sha256_hex(data: str) -> str:
    return hashlib.sha256(data.encode()).hexdigest()


def _sign(message: str, secret: str) -> str:
    return hmac.new(secret.encode(), message.encode(), hashlib.sha256).hexdigest().upper()


# ---------------------------------------------------------------------
# Sesión Tuya: un solo access_token compartido, renovado bajo demanda
# ---------------------------------------------------------------------
class TuyaSession:
    def __init__(self, cfg: TuyaConfig, http: aiohttp.ClientSession):
        self.cfg = cfg
        self.http = http
        self._access_token: Optional[str] = None
        self._expires_at: float = 0
        self._lock = asyncio.Lock()

    async def get_token(self) -> str:
        async with self._lock:
            if self._access_token and time.time() < self._expires_at - self.cfg.token_refresh_margin:
                return self._access_token
            await self._refresh_token()
            return self._access_token

    async def _refresh_token(self):
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
        _LOGGER.info("Token Tuya renovado (expira en %ss)", data["result"]["expire_time"])

    async def allocate_stream(self) -> str:
        """Pide a Tuya una URL de stream RTSP nativa (no HLS: el HLS de
        Tuya resultó ser un preview de ~12-16s pensado para thumbnails,
        no para reproducción sostenida)."""
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
        return data["result"]["url"]


# ---------------------------------------------------------------------
# Puente ffmpeg: Tuya HLS -> RTSP local (mediamtx)
# ---------------------------------------------------------------------
class FfmpegBridge:
    """Lanza y vigila un único proceso ffmpeg que remuxea el RTSP nativo
    de Tuya hacia el RTSP local (mediamtx). Si el proceso muere (URL
    expirada, corte de red, etc.) pide una URL nueva a Tuya y reinicia."""

    def __init__(self, cfg: TuyaConfig, tuya: TuyaSession):
        self.cfg = cfg
        self.tuya = tuya
        self._proc: Optional[asyncio.subprocess.Process] = None

    async def run_forever(self):
        while True:
            try:
                url = await self.tuya.allocate_stream()
                _LOGGER.info("Nueva URL de stream RTSP de Tuya obtenida, iniciando ffmpeg")
                await self._run_ffmpeg(url)
            except Exception as err:
                _LOGGER.warning("Fallo en el bridge ffmpeg: %s", err)
            await asyncio.sleep(self.cfg.ffmpeg_restart_backoff)

    async def _run_ffmpeg(self, tuya_rtsp_url: str):
        cmd = [
            "ffmpeg",
            "-loglevel", "warning",
            "-fflags", "nobuffer",
            # Transporte y timeout de lectura para la conexión RTSP
            # de ENTRADA (hacia Tuya). Si Tuya deja de mandar datos
            # por 8s, ffmpeg falla en vez de quedarse colgado, y el
            # bucle de arriba pide una URL nueva.
            "-rtsp_transport", "tcp",
            "-timeout", "8000000",
            "-i", tuya_rtsp_url,
            # RTSP nativo trae los parámetros de codec (SDP) correctos,
            # así que audio y video se copian tal cual, sin recodificar
            # ni el workaround de -an que necesitaba el HLS.
            "-c", "copy",
            "-f", "rtsp",
            # Transporte para la conexión de SALIDA (hacia mediamtx).
            "-rtsp_transport", "tcp",
            self.cfg.rtsp_target,
        ]
        self._proc = await asyncio.create_subprocess_exec(*cmd)
        returncode = await self._proc.wait()
        _LOGGER.warning("ffmpeg terminó con código %s, se pedirá una URL nueva", returncode)

    def stop(self):
        if self._proc and self._proc.returncode is None:
            self._proc.terminate()


async def main():
    cfg = TuyaConfig.from_env()
    async with aiohttp.ClientSession() as http:
        tuya = TuyaSession(cfg, http)
        bridge = FfmpegBridge(cfg, tuya)
        await bridge.run_forever()


if __name__ == "__main__":
    asyncio.run(main())
