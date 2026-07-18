# TuyaCam RTSP Bridge

**One cloud session. Every local client.**

Tuya's camera API wasn't built for multiple simultaneous viewers —
each client that connects opens its own cloud session, and Tuya
throttles or drops connections when too many pile up. TuyaCam RTSP Bridge
solves this at the source: it opens **one** authenticated session with
Tuya's cloud, pulls the native RTSP stream, and re-serves it locally
over a standard RTSP endpoint that any number of local clients can
consume freely.

No more juggling credentials across integrations. No more "camera
unavailable" errors when Frigate, Home Assistant, and a VLC preview
all try to watch at once. Just one clean local stream, always on,
auto-healing.

**Built for:** Home Assistant · Frigate · VLC · Alexa Smart Home · any
RTSP-compatible client

> **Disclaimer:** Not affiliated with, endorsed by, or supported by
> Tuya Inc. Relies on an unofficial use of Tuya's developer API that
> may change or stop working at any time. **Not intended for
> life-safety or critical security use.** The local RTSP stream is
> **unauthenticated by default** — secure your own network. You are
> responsible for complying with Tuya's Developer Platform Service
> Agreement and any privacy/recording laws that apply to your camera's
> field of view. Provided "as is", with no warranty — see the
> [full disclaimer and LICENSE](https://github.com/chias10/tuyacam-bridge-addons)
> in the repository.

## Before installing

You'll need, from your [Tuya IoT developer console](https://iot.tuya.com/):

- `client_id`
- `client_secret`
- Your camera's `device_id`

Your camera must have **RTSP service enabled** on Tuya's side (not
every model supports it — if the `allocate` call with `type: RTSP`
fails, your camera likely only supports Tuya's short HLS preview,
which isn't suitable for sustained playback).

## Configuration

| Option | Description |
|---|---|
| `tuya_base_url` | Tuya's regional API endpoint (`https://openapi.tuyaus.com` for US, `https://openapi.tuyaeu.com` for EU, etc.) |
| `tuya_client_id` | Client ID from your Tuya IoT project (shared by all cameras) |
| `tuya_client_secret` | Client Secret from your Tuya IoT project (shared by all cameras) |
| `cameras` | A list of cameras, each with a `name` (used as the local RTSP path), a `device_id`, and an optional `stream_type` |

`stream_type` picks which of the camera's encoded profiles to pull:
- `0` (default): main stream, typically HD.
- `1`: sub-stream, typically SD — useful for cameras you mostly want
  for motion detection rather than high-res viewing, to save bandwidth.

Actual resolution/bitrate for each depends on your camera's model and
firmware.

Example with three cameras, one on the sub-stream to save bandwidth:

```yaml
cameras:
  - name: entrada
    device_id: "abc123"
    stream_type: 0
  - name: cochera
    device_id: "def456"
    stream_type: 0
  - name: patio
    device_id: "ghi789"
    stream_type: 1
```

## Usage

Once running, each camera's stream is available at:

```
rtsp://<your-ha-ip>:8554/<camera-name>
```

For the example above: `rtsp://<your-ha-ip>:8554/entrada`,
`.../cochera`, `.../patio`.

- **Home Assistant**: use a generic RTSP camera pointing to that URL,
  one per camera.
- **Frigate**: add each one as a normal input in `frigate.yml`.
- **VLC**: open it directly via "Open Network Stream".
- **Alexa**: if a camera is already exposed in HA via the Smart Home
  skill, it inherits its stream automatically.
- **Metrics**: `http://<your-ha-ip>:9101/metrics` (Prometheus format),
  with a `camera` label on every per-camera metric.

## Troubleshooting

Check the add-on's **Log**. If you see constant reconnects, or
"stream is available" followed by a drop a few seconds later, your
camera likely doesn't support Tuya's native RTSP and only offers the
short HLS preview — this add-on isn't built for that case.
