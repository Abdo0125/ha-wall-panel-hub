ARG BUILD_FROM
FROM ${BUILD_FROM}

WORKDIR /app
# The intercom (intercom.py): Asterisk with pjsip; SRTP for panels over WebRTC.
RUN apk add --no-cache asterisk asterisk-srtp

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY wallpanel_hub ./wallpanel_hub

CMD ["python3", "-m", "wallpanel_hub"]
