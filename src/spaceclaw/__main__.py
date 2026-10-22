"""Command line entry point: `spaceclaw serve`."""

from __future__ import annotations

import argparse
import asyncio
import logging

import uvicorn

from .bbs import BBS
from .config import load_config
from .db import Database
from .flickr import Flickr
from .telnet import TelnetConnection
from .web import create_app

log = logging.getLogger("spaceclaw")


async def serve(secrets_path: str | None) -> None:
    config = load_config(secrets_path)
    db = Database(config.db_path)
    bbs = BBS(config, db, Flickr(config.flickr_api_key, config.flickr_api_secret))

    loop = asyncio.get_running_loop()
    telnet_server = await loop.create_server(
        lambda: TelnetConnection(bbs.on_connect), config.telnet_host, config.telnet_port
    )
    log.info("telnet listening on %s:%d", config.telnet_host, config.telnet_port)

    web_server = uvicorn.Server(
        uvicorn.Config(
            create_app(bbs),
            host=config.web_host,
            port=config.web_port,
            proxy_headers=True,
            log_config=None,
        )
    )
    log.info("web listening on %s:%d (public URL %s)", config.web_host, config.web_port, config.public_url)
    if config.dev:
        log.info("dev mode: backdoor login %s", "enabled" if config.backdoor_passphrase else "disabled")

    try:
        async with telnet_server:
            # uvicorn installs SIGINT/SIGTERM handlers and returns from serve() on shutdown.
            await web_server.serve()
    finally:
        db.close()


def main() -> None:
    parser = argparse.ArgumentParser(prog="spaceclaw", description="Almost certainly the best Flickr BBS.")
    parser.add_argument("--secrets", help="path to secrets.json (default: config/secrets.json)")
    sub = parser.add_subparsers(dest="command")
    sub.add_parser("serve", help="run the telnet BBS and web login server (default)")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    asyncio.run(serve(args.secrets))


if __name__ == "__main__":
    main()
