# Security Policy

## Supported Versions

Only the latest published version of TuyaCam Bridge receives security
fixes. Please update to the latest version before reporting an issue.

## Known Security Considerations

- **The local RTSP stream is unauthenticated by default.** Any device
  on your local network that can reach the configured port (`8554` by
  default) can view the stream. This is a design tradeoff for
  simplicity and local-network use — it is **not** intended to be
  exposed to the internet. If you need to access it remotely, do so
  through Home Assistant's own authenticated remote access (Nabu Casa,
  a properly configured reverse proxy with auth, or a VPN), never by
  port-forwarding `8554` directly.
- **Tuya credentials** (`client_id`, `client_secret`, `device_id`) are
  stored by Home Assistant Supervisor as add-on configuration and used
  only to call Tuya's official API endpoints from within the add-on's
  container. They are not logged, and not transmitted anywhere other
  than Tuya's API.

## Reporting a Vulnerability

If you find a security issue, please report it privately rather than
opening a public GitHub issue:

- Open a [GitHub Security Advisory](https://github.com/chia10/tuya-stream-proxy-addons/security/advisories/new)
  for this repository, **or**
- Email: aziel.cuevasf@gmail.com

Please include steps to reproduce and the potential impact. We'll do
our best to respond promptly, but note this is a community-maintained,
best-effort project with no SLA.
