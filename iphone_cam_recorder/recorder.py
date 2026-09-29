#!/usr/bin/env python3
"""iPhone Cam Auto Recorder.

Localiza dispositivos por MAC (la IP cambia por DHCP), comprueba si su URL
de cámara emite video y, si es así, graba con ffmpeg en segmentos.
"""
import json
import os
import re
import shutil
import subprocess
import threading
import time
import urllib.request
from datetime import datetime

OPTIONS = json.load(open("/data/options.json"))
SCAN_INTERVAL = int(OPTIONS.get("scan_interval", 15))
SEGMENT_SECONDS = int(OPTIONS.get("segment_minutes", 10)) * 60
RETENTION_DAYS = int(OPTIONS.get("retention_days", 14))
REC_DIR = OPTIONS.get("recordings_dir") or "/media/iphone_recordings"
NOTIFY = (OPTIONS.get("notify_service") or "").strip()
IFACE = (OPTIONS.get("interface") or "").strip()

MAC_RE = re.compile(r"([0-9a-f]{2}(?::[0-9a-f]{2}){5})", re.I)
ip_by_mac = {}          # mac -> (ip, timestamp)
lock = threading.Lock()


def log(msg):
    print(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {msg}", flush=True)


def norm_mac(mac):
    return mac.strip().lower().replace("-", ":")


def notify(msg):
    token = os.environ.get("SUPERVISOR_TOKEN")
    if not (NOTIFY and token):
        return
    domain, _, svc = NOTIFY.partition(".")
    if not svc:
        domain, svc = "notify", NOTIFY
    req = urllib.request.Request(
        f"http://supervisor/core/api/services/{domain}/{svc}",
        data=json.dumps({"message": msg, "title": "iPhone Cam"}).encode(),
        headers={"Authorization": f"Bearer {token}",
                 "Content-Type": "application/json"})
    try:
        urllib.request.urlopen(req, timeout=10).read()
    except Exception as e:
        log(f"notify falló: {e}")


def scan_network():
    """Actualiza ip_by_mac con arp-scan y la tabla de vecinos."""
    found = {}
    cmd = ["arp-scan", "-q", "-r", "3"]
    cmd += ["-I", IFACE] if IFACE else ["-l"]
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=60).stdout
        for line in out.splitlines():
            parts = line.split()
            if len(parts) >= 2 and MAC_RE.fullmatch(parts[1]):
                found[norm_mac(parts[1])] = parts[0]
    except Exception as e:
        log(f"arp-scan falló: {e}")
    try:
        out = subprocess.run(["ip", "neigh"], capture_output=True, text=True,
                             timeout=10).stdout
        for line in out.splitlines():
            m = MAC_RE.search(line)
            if m and re.search(r"REACHABLE|DELAY|PROBE", line):
                found.setdefault(norm_mac(m.group(1)), line.split()[0])
    except Exception:
        pass
    now = time.time()
    with lock:
        for mac, ip in found.items():
            ip_by_mac[mac] = (ip, now)


def scanner():
    while True:
        scan_network()
        time.sleep(SCAN_INTERVAL)


def current_ip(mac):
    # Solo se acepta si el escaneo reciente la vio (evita IPs viejas)
    with lock:
        entry = ip_by_mac.get(mac)
    if entry and time.time() - entry[1] <= SCAN_INTERVAL * 3 + 10:
        return entry[0]
    return None


def has_video(url):
    try:
        p = subprocess.run(
            ["ffprobe", "-v", "error", "-rw_timeout", "8000000",
             "-select_streams", "v:0", "-show_entries", "stream=codec_type",
             "-of", "csv=p=0", url],
            capture_output=True, text=True, timeout=20)
        return "video" in p.stdout
    except Exception:
        return False


def record(dev, url, ip):
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
    notify(f"{dev['name']}: grabando ({ip})")
    proc = subprocess.Popen(cmd)
    mac = norm_mac(dev["mac"])
    while proc.poll() is None:
        time.sleep(5)
        new_ip = current_ip(mac)
        if new_ip and new_ip != ip:   # la IP cambió: reiniciar con la nueva
            log(f"[{dev['name']}] IP cambió {ip} -> {new_ip}, reiniciando")
            proc.terminate()
            break
    try:
        proc.wait(timeout=15)
    except subprocess.TimeoutExpired:
        proc.kill()
    log(f"[{dev['name']}] grabación terminada")


def device_loop(dev):
    mac = norm_mac(dev["mac"])
    last_state = None
    while True:
        ip = current_ip(mac)
        if not ip:
            if last_state != "offline":
                log(f"[{dev['name']}] fuera de la red")
                last_state = "offline"
            time.sleep(SCAN_INTERVAL)
            continue
        if last_state == "offline" or last_state is None:
            log(f"[{dev['name']}] conectado con IP {ip}")
        url = dev["stream_url"].replace("{ip}", ip)
        if has_video(url):
            last_state = "recording"
            record(dev, url, ip)
            time.sleep(3)
        else:
            if last_state != "no_video":
                log(f"[{dev['name']}] en red ({ip}) pero sin video en {url}")
                last_state = "no_video"
            time.sleep(SCAN_INTERVAL)


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
    if not devices:
        log("No hay dispositivos configurados")
        return
    if not shutil.which("arp-scan"):
        log("arp-scan no disponible")
        return
    scan_network()
    for target in [scanner, cleaner] + [lambda d=d: device_loop(d) for d in devices]:
        threading.Thread(target=target, daemon=True).start()
    while True:
        time.sleep(3600)


if __name__ == "__main__":
    main()
