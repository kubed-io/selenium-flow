"""Entry point: the config, loaded from file, env and args, into a running server."""

from __future__ import annotations

import logging

from . import config
from .server import SeleniumMCP

log = logging.getLogger(__name__)


def main(argv: list[str] | None = None) -> None:
    """Entry point for the ``selenium-flow`` console script."""
    try:
        loaded = config.load(argv)
    except config.ConfigError as exc:
        # A config this server cannot run on stops the boot with the reason:
        # restarted until fixed beats started on something misread (§F4.12).
        raise SystemExit(f"selenium-flow: {exc}") from None
    settings = loaded.settings
    logging.basicConfig(level=settings.log_level.upper())
    server = SeleniumMCP(settings, sources=loaded.sources)
    log.info(
        "config=%s grid=%s auth=%s sessions=%s skill=%s flows=%s secrets=%s",
        settings.config_file or "none",
        settings.grid.url,
        "on" if server.auth_token else "off",
        server.sessions.kind,
        server.skill.skill_info.name if server.skill else "off",
        server.flows.kind if server.flows else "off",
        len(server.secrets.sources) if server.secrets else "off",
    )
    server.run(transport=settings.transport, host=settings.host, port=settings.port)


if __name__ == "__main__":
    main()
