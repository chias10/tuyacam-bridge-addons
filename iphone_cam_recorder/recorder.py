#!/usr/bin/env python3
"""iPhone Cam Auto Recorder.

Escucha (websocket) el sensor de trigger de la app (launch) y el sensor con
la URL de la cámara; en cada launch intenta grabar ATTEMPT segundos.
Si la URL no es alcanzable (iPhone fuera de la LAN), prueba la misma URL con
`fallback_host` (IP/hostname VPN, p. ej. Tailscale).
"""
import asyncio
import json
import os
import re
import subprocess
import threading
import time
import urllib.request
from datetime import datetime
from urllib.parse import urlsplit, urlunsplit

import aiohttp

OPTIONS = json.load(open("/data/options.json"))
ATTEMPT = int(OPTIONS.get("attempt_seconds", 15))
SEGMENT_SECONDS = int(OPTIONS.get("segment_minutes", 10)) * 60
RETENTION_DAYS = int(OPTIONS.get("retention_days", 14))
REC_DIR = OPTIONS.get("recordings_dir") or "/media/iphone_recordings"
NOTIFY = (OPTIONS.get("notify_service") or "").strip()
TOKEN = os.environ.get("SUPERVISOR_TOKEN", "")
API = "http://supervisor/core/api"
WS = "ws://supervisor/core/websocket"

urls = {}                # entity_id -> última URL conocida
launch_events = {}       # trigger_entity -> [(dev, threading.Event)]
lock = threading.Lock()


def log(msg):
    print(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {msg}", flush=True)


def extract_url(state):
    if not state:
        return None
    attrs = state.get("attributes") or {}
    url = attrs.get("stream_url") or attrs.get("Stream URL")
    if not url and str(state.get("state", "")).startswith(("http", "rtsp")):
        url = state["state"]
    return url or None


def set_url(entity, state):
    url = extract_url(state)
    if not url:
        return
    with lock:
        if urls.get(entity) != url:
            log(f"[{entity}] URL: {url}")
        urls[entity] = url


def notify(msg):
    if not (NOTIFY and TOKEN):
        return
    domain, _, svc = NOTIFY.partition(".")
    if not svc:
        domain, svc = "notify", NOTIFY
    req = urllib.request.Request(
        f"{API}/services/{domain}/{svc}",
        data=json.dumps({"message": msg, "title": "iPhone Cam"}).encode(),
        headers={"Authorization": f"Bearer {TOKEN}",
                 "Content-Type": "application/json"})
    try:
        urllib.request.urlopen(req, timeout=10).read()
    except Exception as e:
        log(f"notify falló: {e}")


async def listen(entities):
    """Suscripción a state_changed con reconexión."""
    while True:
        try:
            async with aiohttp.ClientSession() as s:
                # estado inicial
                for e in entities:
                    async with s.get(f"{API}/states/{e}", headers={
                            "Authorization": f"Bearer {TOKEN}"}) as r:
                        if r.status == 200:
                            set_url(e, await r.json())
                async with s.ws_connect(WS, heartbeat=30) as ws:
                    await ws.receive_json()                       # auth_required
                    await ws.send_json({"type": "auth", "access_token": TOKEN})
                    if (await ws.receive_json()).get("type") != "auth_ok":
                        raise RuntimeError("auth websocket falló")
                    await ws.send_json({"id": 1, "type": "subscribe_events",
                                        "event_type": "state_changed"})
                    log("escuchando eventos de Home Assistant")
                    async for msg in ws:
                        if msg.type != aiohttp.WSMsgType.TEXT:
                            break
                        ev = json.loads(msg.data)
                        data = (ev.get("event") or {}).get("data") or {}
                        eid = data.get("entity_id")
                        if eid in entities:
                            set_url(eid, data.get("new_state"))
                        for dev, evt in launch_events.get(eid, []):
                            st = (data.get("new_state") or {}).get("state")
                            if st == (dev.get("trigger_state") or "launch"):
                                log(f"[{dev['name']}] trigger '{st}' detectado")
                                evt.set()
        except Exception as e:
            log(f"websocket: {e}; reintentando en 10s")
        await asyncio.sleep(10)


def with_host(url, host):
    p = urlsplit(url)
    port = f":{p.port}" if p.port else ""
    return urlunsplit((p.scheme, host + port, p.path, p.query, p.fragment))


def candidates(dev):
    with lock:
        url = urls.get(dev["entity"])
    if not url:
        return []
    out = [url]
    fb = (dev.get("fallback_host") or "").strip()
    if fb:
        out.append(with_host(url, fb))
    return out


def has_video(url):
    try:
        p = subprocess.run(
            ["ffprobe", "-v", "error", "-rw_timeout", "4000000",
             "-select_streams", "v:0", "-show_entries", "stream=codec_type",
             "-of", "csv=p=0", url],
            capture_output=True, text=True, timeout=8)
        return "video" in p.stdout
    except Exception:
        return False


def record(dev, url):
    out_dir = os.path.join(REC_DIR, dev["name"])
    os.makedirs(out_dir, exist_ok=True)
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "warning", "-rw_timeout", "10000000"]
    if url.startswith("rtsp"):
        cmd += ["-rtsp_transport", "tcp"]
    cmd += ["-i", url, "-map", "0:v:0", "-map", "0:a?", "-c", "copy",
            "-f", "segment", "-segment_time", str(SEGMENT_SECONDS),
            "-reset_timestamps", "1", "-strftime", "1",
            os.path.join(out_dir, "%Y%m%d_%H%M%S.mkv")]
    log(f"[{dev['name']}] grabando {url}")
    notify(f"{dev['name']}: grabando")
    proc = subprocess.Popen(cmd)
    finished = True
    while proc.poll() is None:
        time.sleep(3)
        if url not in candidates(dev):   # el sensor publicó otra URL/IP
            log(f"[{dev['name']}] URL cambió, reiniciando")
            proc.terminate()
            finished = False
            break
    try:
        proc.wait(timeout=8)
    except subprocess.TimeoutExpired:
        proc.kill()
    log(f"[{dev['name']}] grabación terminada")
    return finished


def device_loop(dev, evt):
    """Espera un launch; intenta obtener video ATTEMPT segundos; graba o se rinde."""
    while True:
        evt.wait()
        evt.clear()
        deadline = time.time() + ATTEMPT
        log(f"[{dev['name']}] launch: intentando video ({ATTEMPT}s)")
        ok = None
        while time.time() < deadline and not ok:
            ok = next((u for u in candidates(dev) if has_video(u)), None)
            if not ok:
                time.sleep(2)
        if not ok:
            log(f"[{dev['name']}] sin video en {ATTEMPT}s, esperando siguiente launch")
            continue
        if not record(dev, ok):
            evt.set()      # cambió la URL: reintentar con la nueva
        else:
            log(f"[{dev['name']}] esperando siguiente launch")


def cleaner():
    while RETENTION_DAYS > 0:
        cutoff = time.time() - RETENTION_DAYS * 86400
        for root, _, files in os.walk(REC_DIR):
            for f in files:
                p = os.path.join(root, f)
                try:
                    if os.path.getmtime(p) < cutoff:
                        os.remove(p)
                except OSError:
                    pass
        time.sleep(3600)


def main():
    os.makedirs(REC_DIR, exist_ok=True)
    devices = OPTIONS.get("devices", [])
    if not devices or not TOKEN:
        log("Faltan dispositivos o SUPERVISOR_TOKEN")
        return
    threading.Thread(target=cleaner, daemon=True).start()
    for d in devices:
        evt = threading.Event()
        launch_events.setdefault(d["trigger_entity"], []).append((d, evt))
        threading.Thread(target=device_loop, args=(d, evt), daemon=True).start()
    watch = {d["entity"] for d in devices} | set(launch_events)
    asyncio.run(listen(watch))


if __name__ == "__main__":
    main()
