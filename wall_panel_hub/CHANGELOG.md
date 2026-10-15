# Changelog

## 0.2.0

- Remote panels: the add-on installs a companion integration that opens
  `/api/wall_panel_hub/` on Home Assistant's own address (local, Nabu Casa, Cloudflare
  tunnel...). Panels on other networks pair and connect through it.
- After the first start, restart Home Assistant and accept **Wall Panel Hub** under
  Settings → Devices & services → Discovered.

## 0.1.2

- Fix: the add-on started in developer mode inside Home Assistant (the Supervisor
  token is now read where s6-overlay keeps it), so the page and the gateway were refused.

## 0.1.1

- Fix: the Wall Panels page was refused on installs where ingress requests come from
  another address of Home Assistant's internal network.

## 0.1.0

- Secure gateway between wall panels and Home Assistant: panels never hold an HA token,
  and may only control the entities in their own layout.
- Pairing with a 6-digit code approved from the Wall Panels sidebar page; one-click revoke.
- Per-panel layouts, validated and versioned, pushed to the panel on every change.
- mDNS announcement (`_wallpanel-hub._tcp`) so panels find the hub on the LAN.
