rtspAddress: :8554
webrtcAddress: :8889

paths:
  {{RTSP_PATH}}:
    # tuya_stream_proxy.py hace el push (publish) aquí con ffmpeg.
    # HA, Frigate, VLC y Alexa (vía HA) hacen pull desde
    # rtsp://<host>:8554/{{RTSP_PATH}} sin tocar a Tuya.
    source: publisher
