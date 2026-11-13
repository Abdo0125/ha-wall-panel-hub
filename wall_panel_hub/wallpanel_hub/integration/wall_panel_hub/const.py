DOMAIN = "wall_panel_hub"
# As in manifest.json (a test compares them): told to the add-on, which shows when HA runs an older one.
VERSION = "0.8.0"
CONF_HOST = "host"
CONF_PORT = "port"
# Lets the integration in at the add-on's /api/integration (panels as speakers).
CONF_SECRET = "secret"
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8765

# Panels reach the hub here, on Home Assistant's own address (local, Nabu Casa, tunnel...).
URL_BASE = "/api/wall_panel_hub"
