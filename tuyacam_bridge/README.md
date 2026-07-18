# TuyaCam RTSP Bridge — Home Assistant Add-on

[![Build and publish](https://github.com/chia10/tuyacam-bridge-addons/actions/workflows/build.yml/badge.svg)](https://github.com/chia10/tuyacam-bridge-addons/actions/workflows/build.yml)

Keeps a single session with the Tuya cloud and republishes a stable
local native RTSP stream, so Home Assistant, Frigate, VLC, and Alexa
can all watch it simultaneously without each one opening its own
session against Tuya.

**One cloud session. Every local client.**

Tuya's camera API wasn't built for multiple simultaneous viewers —
each client that connects opens its own cloud session, and Tuya
throttles or drops connections when too many pile up. TuyaCam RTSP Bridge
solves this at the source: it opens **one** authenticated session with
Tuya's cloud, pulls the native RTSP stream, and re-serves it locally
over a standard RTSP endpoint that any number of local clients can
consume freely — no more "camera unavailable" errors when Frigate,
Home Assistant, and a VLC preview all try to watch at once.

**Built for:** Home Assistant · Frigate · VLC · Alexa Smart Home · any
RTSP-compatible client

```
                      Tuya Cloud
                          │
                    OAuth + RTSP
                          │
                  TuyaCam RTSP Bridge
                          │
          ┌───────────────┼───────────────┐
          │                │                │
       Frigate             HA              VLC
```

| Without Bridge | With Bridge |
|---|---|
| Multiple cloud sessions (one per client) | One cloud session, shared locally |
| Random disconnects when clients pile up | Stable, self-healing local stream |
| Every app authenticates against Tuya | Single authentication, done once |
| High load on Tuya's cloud | One RTSP relay, unlimited local viewers |

## Disclaimer

> **No affiliation.** This project is independent and not affiliated
> with, endorsed by, sponsored by, or otherwise associated with Tuya
> Inc. or Tuya Smart. "Tuya" and any related marks are trademarks of
> their respective owner and are used here only to describe
> interoperability.
>
> **Unofficial API usage.** This add-on relies on Tuya's public
> developer API in a way that is not officially documented for
> sustained playback (native RTSP allocation). Tuya may change,
> rate-limit, or discontinue this behavior at any time without notice,
> and this project may stop working as a result. Use of the Tuya API
> is subject to Tuya's own Developer Platform Service Agreement — you
> are responsible for complying with it.
>
> **Not for life-safety or critical use.** This software is a
> convenience tool for local video access. It is **not** designed,
> tested, or intended for life-safety, critical security, medical, or
> any other use case where failure could result in harm, loss, or
> injury. Do not rely on it as your sole means of security monitoring.
>
> **Network security.** The local RTSP stream this add-on publishes is
> **unauthenticated by default** — any device on your local network
> that can reach the configured port can view it. Securing your local
> network (firewalling, VLANs, trusted devices only) is your
> responsibility.
>
> **Privacy & recording laws.** If your camera monitors spaces where
> other people may appear (shared entryways, workplaces, rentals,
> etc.), you are solely responsible for complying with applicable
> privacy, surveillance, and recording-consent laws in your
> jurisdiction.
>
> **Credentials.** Your Tuya `client_id`, `client_secret`, and
> `device_id` are stored only in Home Assistant Supervisor's add-on
> configuration and used solely to talk to Tuya's official API
> endpoints. This project does not transmit them anywhere else. Rotate
> them immediately if you believe they've been exposed (e.g. pasted in
> a public chat, forum, or commit).
>
> **No warranty, no liability.** This software is provided "as is",
> without warranty of any kind. The authors and contributors are not
> liable for account suspensions, service disruptions, data loss,
> unauthorized access, or any other direct or indirect damages arising
> from its use. See [LICENSE](LICENSE) for the full legal text.
>
> **Educational and personal use.** This project is intended for
> educational and personal home-automation use. Commercial or
> large-scale deployment is at your own risk and discretion.

## Installation

1. In Home Assistant: **Settings → Add-ons → Add-on Store → ⋮ →
   Repositories**.
2. Add this URL:
   ```
   https://github.com/chia10/tuyacam-bridge-addons
   ```
3. Find **"TuyaCam RTSP Bridge"** in the store (a new section appears
   after adding the repository) and install it.
4. Configure it with your Tuya IoT credentials (see
   [DOCS.md](tuyacam_bridge/DOCS.md)) and start it.

## Add-ons in this repository

| Add-on | Description |
|---|---|
| [TuyaCam RTSP Bridge](tuyacam_bridge) | Stable local RTSP from a Tuya camera |

## Roadmap

- [ ] Multiple cameras (one add-on instance, several Tuya devices)
- [ ] HTTPS support for the metrics endpoint
- [ ] Authentication for the local RTSP stream
- [x] Metrics (Prometheus, `:9101/metrics`)
- [ ] WebRTC output
- [ ] go2rtc backend option (alongside mediamtx)

## Contributing

Issues and PRs are welcome. See
[CHANGELOG.md](tuyacam_bridge/CHANGELOG.md) for the change history.

## Security

Found a security issue? Please see [SECURITY.md](SECURITY.md) instead
of opening a public issue.

## License

[MIT](LICENSE)
