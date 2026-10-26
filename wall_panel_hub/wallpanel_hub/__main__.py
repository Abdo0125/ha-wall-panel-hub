import asyncio
import logging

import aiohttp
from aiohttp import web

from . import companion, discovery, settings
from .catalog import CatalogCache
from .catalog import fetch as fetch_catalog
from .doorbell import Doorbell
from .gateway import Gateway
from .intercom import Intercom
from .speakers import Speakers, load_secret
from .store import Store
from .web import management_ui, panel_api


async def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    cfg = settings.load()
    log = logging.getLogger("wallpanel_hub")
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
        await intercom.apply()
        doorbell = Doorbell(intercom, gateway.speakers.door_event,
                            lambda pid: getattr(store.panels.get(pid), "name", None))
        doorbell_task = asyncio.create_task(doorbell.run())

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
        for app, port in ((panel_api(store, gateway), cfg.panel_port),
                          (management_ui(store, gateway, cfg.ui_allowed, catalog, browser, intercom), cfg.ui_port)):
            runner = web.AppRunner(app)
            await runner.setup()
            await web.TCPSite(runner, "0.0.0.0", port).start()
            runners.append(runner)
        log.info("panel API on :%s, management UI on :%s", cfg.panel_port, cfg.ui_port)

        zc = await discovery.announce(cfg.panel_port)
        if zc:
            browser.start(zc)
        if not cfg.dev:
            restart = companion.install(cfg.ha_config)
            await companion.announce(session, cfg.ha_token, cfg.panel_port, restart, secret)
        try:
            await asyncio.Event().wait()
        finally:
            floors_task.cancel()
            doorbell_task.cancel()
            await intercom.stop()
            if zc:
                await browser.close()
                await zc.async_close()
            for r in runners:
                await r.cleanup()


if __name__ == "__main__":
    asyncio.run(main())
