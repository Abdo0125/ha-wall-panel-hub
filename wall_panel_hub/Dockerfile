ARG BUILD_FROM
FROM ${BUILD_FROM}

WORKDIR /app
# The intercom (intercom.py): Asterisk with pjsip; SRTP for panels over WebRTC.
RUN apk add --no-cache asterisk asterisk-srtp
# AirPlay (airplay.py): shairport-sync for each panel, run as its own unprivileged user;
# avahi (over D-Bus) announces them on the home network.
RUN apk add --no-cache shairport-sync avahi dbus su-exec && adduser -S -D -H -s /sbin/nologin airplay
# Setting up panels over adb from the management page (provision.py): Apache-2.0 / MIT.
RUN apk add --no-cache android-tools

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY wallpanel_hub ./wallpanel_hub
# The panel app this add-on carries, from its own GitHub release (panel_app.py); verified.
RUN python3 -m wallpanel_hub.fetch_app

CMD ["python3", "-m", "wallpanel_hub"]
