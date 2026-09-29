ARG BUILD_FROM=ghcr.io/home-assistant/base:3.24
FROM $BUILD_FROM

RUN apk add --no-cache python3 ffmpeg arp-scan iproute2 jq curl

COPY recorder.py /opt/recorder.py
COPY run.sh /
RUN chmod a+x /run.sh

CMD [ "/run.sh" ]
