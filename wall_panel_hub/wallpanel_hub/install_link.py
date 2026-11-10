"""A temporary local link that installs the panel app on a new panel (docs/distribution-plan.md, D3).

The installer opens it from the management page; on the new panel, the browser goes to
http://<hub>:<panel port>/get/<code> and downloads the app this add-on carries (panel_app.py).
The panel port, because a new panel has no Home Assistant login for the management page.
The app is no secret (it is on GitHub too), yet the path stays shut unless the installer opens it:
- a 4-digit code, in memory only, open for OPEN_S, closed by the installer or a restart;
- MAX_WRONG wrong codes close it.
"""

import html
import secrets
import time

from . import discovery, panel_app

OPEN_S = 30 * 60
MAX_WRONG = 10


class InstallLink:
    def __init__(self, panel_port: int, now=time.monotonic):
        self.panel_port = panel_port
        self._now = now
        self._code: str | None = None
        self._until = 0.0
        self._wrong = 0

    def open(self) -> dict:
        """A new code, open for OPEN_S (any earlier one stops working)."""
        self._code = f"{secrets.randbelow(10000):04d}"
        self._until = self._now() + OPEN_S
        self._wrong = 0
        return self.status()

    def close(self) -> None:
        self._code = None

    def _live(self) -> str | None:
        if self._code and self._now() >= self._until:
            self._code = None
        return self._code

    def check(self, code: str) -> bool:
        """True for the open code. A wrong one counts; MAX_WRONG close the link."""
        live = self._live()
        if live is None:
            return False
        if secrets.compare_digest(code, live):
            return True
        self._wrong += 1
        if self._wrong >= MAX_WRONG:
            self._code = None
        return False

    def status(self) -> dict:
        """For the management page: the app, and the link if open."""
        live = self._live()
        link = None
        if live:
            link = {
                "url": f"http://{discovery.lan_address()}:{self.panel_port}/get/{live}",
                "seconds_left": max(0, int(self._until - self._now())),
            }
        return {"app": panel_app.info(), "link": link}


# ---- the page the new panel's browser shows: in its language ----

TEXT = {
    "en": {
        "from": "Version {0} · from Wall Panel Hub on this network",
        "download": "Download the app",
        "step1": "Open the downloaded file and tap Install. If Android asks, allow this browser to install apps, then go back.",
        "step2": "Open Wall Panel: it finds the hub and shows a code to approve in the add-on.",
    },
    "ar": {
        "from": "النسخة {0} · من Wall Panel Hub في هذه الشبكة",
        "download": "تنزيل التطبيق",
        "step1": "افتح الملف الذي نزل واضغط تثبيت. إن طلب أندرويد ذلك، اسمح لهذا المتصفح بتثبيت التطبيقات ثم ارجع.",
        "step2": "افتح Wall Panel: يجد الـ Hub ويعرض رمزاً توافق عليه في الـ add-on.",
    },
    "fr": {
        "from": "Version {0} · depuis Wall Panel Hub sur ce réseau",
        "download": "Télécharger l'application",
        "step1": "Ouvrez le fichier téléchargé et touchez Installer. Si Android le demande, autorisez ce navigateur à installer des applications, puis revenez.",
        "step2": "Ouvrez Wall Panel : il trouve le hub et affiche un code à approuver dans l'add-on.",
    },
    "es": {
        "from": "Versión {0} · desde Wall Panel Hub en esta red",
        "download": "Descargar la aplicación",
        "step1": "Abre el archivo descargado y toca Instalar. Si Android lo pide, permite que este navegador instale aplicaciones y vuelve.",
        "step2": "Abre Wall Panel: encuentra el hub y muestra un código para aprobar en el add-on.",
    },
}


def language(accept: str | None) -> str:
    """The first of the four the browser asks for; English otherwise."""
    for part in (accept or "").split(","):
        lang = part.split(";")[0].strip().lower()[:2]
        if lang in TEXT:
            return lang
    return "en"


def page(code: str, accept: str | None) -> str:
    lang = language(accept)
    t = TEXT[lang]
    app = panel_app.expected() or {}
    version = html.escape(str(app.get("version_name", "")))
    file_name = html.escape(panel_app.asset_name(app.get("version_name", "")))
    direction = "rtl" if lang == "ar" else "ltr"
    return f"""<!doctype html>
<html lang="{lang}" dir="{direction}"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Wall Panel</title>
<style>
body {{ margin: 0; background: #0B0B0B; color: #F2F2F2; font-family: sans-serif; display: flex; justify-content: center; }}
main {{ max-width: 460px; padding: 48px 24px; text-align: center; }}
h1 {{ font-size: 26px; font-weight: 500; margin: 0; }}
p.from {{ color: #8A8A8A; font-size: 15px; margin: 6px 0 0; }}
a.get {{ display: inline-block; margin-top: 28px; background: #F2F2F2; color: #0B0B0B; padding: 16px 24px; font-size: 18px; text-decoration: none; }}
ol {{ text-align: start; color: #A6A6A6; font-size: 15px; line-height: 1.8; margin: 28px 0 0; padding-inline-start: 20px; }}
</style></head>
<body><main>
<h1>Wall Panel</h1>
<p class="from">{html.escape(t["from"]).format(version)}</p>
<a class="get" href="{html.escape(code)}/{file_name}" download="{file_name}">{html.escape(t["download"])}</a>
<ol><li>{html.escape(t["step1"])}</li><li>{html.escape(t["step2"])}</li></ol>
</main></body></html>"""


def file_name() -> str:
    """The app's file as the panel saves it: with its version, so an older download is told apart."""
    app = panel_app.expected() or {}
    return panel_app.asset_name(app.get("version_name", ""))
