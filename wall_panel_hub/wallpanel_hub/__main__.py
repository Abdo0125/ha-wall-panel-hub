import asyncio
import logging
import signal

import aiohttp
from aiohttp import web

from . import companion, discovery, settings
from .airplay import AirPlay
from .catalog import CatalogCache
from .catalog import fetch as fetch_catalog
from .doorbell import Doorbell
from .gateway import Gateway
from .install_link import InstallLink
from .provision import Adb, Provisioner
from .intercom import Intercom
from .speakers import Speakers, load_secret
from .store import Store
from .web import management_ui, panel_api


log = logging.getLogger("wallpanel_hub")

# A service that stopped starts again after this long.
RESTART_S = 30


async def keep_running(name: str, run, pause: float = RESTART_S) -> None:
    """[run] again each time it ends: a service of the hub never stops unnoticed."""
    while True:
        try:
            await run()
            log.warning("%s stopped: starting it again", name)
        except Exception:
            log.exception("%s failed: starting it again", name)
        await asyncio.sleep(pause)


async def optional(name: str, step) -> None:
    """A step the panels can do without: failing, it is logged and the hub goes on."""
    try:
        await step
    except Exception:
        log.exception("%s failed: the hub goes on without it", name)


async def set_up_companion(cfg, session, secret: str) -> None:
    restart = companion.install(cfg.ha_config)
    await companion.announce(session, cfg.ha_token, cfg.panel_port, restart, secret)


async def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    cfg = settings.load()
    log.info("Wall Panel Hub %s (%s mode), data in %s", settings.VERSION, "dev" if cfg.dev else "add-on", cfg.data_dir)

    store = Store(cfg.data_dir)
    browser = discovery.PanelBrowser()
    async with aiohttp.ClientSession() as session:
        gateway = Gateway(cfg, store, session)
        secret = load_secret(cfg.data_dir)
        gateway.speakers = Speakers(store, gateway, secret)
        intercom = Intercom(cfg.data_dir, cfg.sip_port)
        gateway.intercom = intercom
        intercom.panel_names = lambda: {pid: p.name for pid, p in store.panels.items()}
        await optional("the intercom", intercom.apply())
        gateway.airplay = AirPlay(cfg.data_dir, store, gateway.speaker_command, gateway.speakers.changed,
                                  gateway.speakers.art_changed)
        await optional("AirPlay", gateway.airplay.apply())
        doorbell = Doorbell(intercom, gateway.speakers.door_event,
                            lambda pid: getattr(store.panels.get(pid), "name", None))
        doorbell_task = asyncio.create_task(keep_running("the doorbell", doorbell.run))
        # The servers' links, for HA.
        links_task = asyncio.create_task(keep_running("the servers' links", lambda: intercom.watch(gateway.speakers.changed)))

        async def floors_changed(cat: dict) -> None:
            if store.save_floors(cat):
                await gateway.notify_config()

        catalog = CatalogCache(lambda: fetch_catalog(session, cfg.ha_ws_url, cfg.ha_token), on_fetch=floors_changed)
        gateway.catalog = catalog

        async def watch_floors() -> None:
            # The rooms menu follows HA's floors without the editor being opened.
            while True:
                try:
                    await catalog.get(refresh=True)
                except Exception as e:  # HA restarting...: next time
                    log.info("floors not read: %s", e)
                await asyncio.sleep(600)

        floors_task = asyncio.create_task(watch_floors())

        runners = []
        # The link that installs the app on a new panel: opened in the management page, used on the panel port.
        install = InstallLink(cfg.panel_port)
        # Setting up panels over adb from the management page (provision.py).
        provisioner = Provisioner(store, cfg.panel_port, Adb(cfg.data_dir))
        for app, port in ((panel_api(store, gateway, install), cfg.panel_port),
                          (management_ui(store, gateway, cfg.ui_allowed, catalog, browser, intercom, install, provisioner), cfg.ui_port)):
            # No line per request: the add-on page asks every few seconds and a camera's video
            # in many small parts, burying what the hub itself logs.
            runner = web.AppRunner(app, access_log=None)
            await runner.setup()
            await web.TCPSite(runner, "0.0.0.0", port).start()
            runners.append(runner)
        log.info("panel API on :%s, management UI on :%s", cfg.panel_port, cfg.ui_port)

        zc = await discovery.announce(cfg.panel_port)
        if zc:
            browser.start(zc)
        if not cfg.dev:
            await optional("the companion integration", set_up_companion(cfg, session, secret))

        # Stopping the add-on: the Supervisor's SIGTERM ends this cleanly, Asterisk and AirPlay too.
        stop = asyncio.Event()
        for sig in (signal.SIGTERM, signal.SIGINT):
            try:
                asyncio.get_running_loop().add_signal_handler(sig, stop.set)
            except NotImplementedError:  # Windows, in developer mode
                pass
        try:
            await stop.wait()
        finally:
            floors_task.cancel()
            doorbell_task.cancel()
            links_task.cancel()
            await intercom.stop()
            await gateway.airplay.stop()
            if zc:
                await browser.close()
                await zc.async_close()
            for r in runners:
                await r.cleanup()


if __name__ == "__main__":
    asyncio.run(main())
