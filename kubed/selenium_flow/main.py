"""Entry point: the config, loaded from file, env and args, into a running server."""

from __future__ import annotations

import logging

from . import config
from .errors import without_userinfo
from .server import SeleniumMCP

log = logging.getLogger(__name__)


def _secrets_summary(catalogue) -> str:
    """"off", or how many directories and how many config entries feed it.

    "secrets=0" used to mean either off or entries-only, which read as a
    contradiction next to a config that plainly turned secrets on.
    """
    if catalogue is None:
        return "off"
    dirs = len(catalogue.sources)
    entries = len(catalogue.config.entries) if catalogue.config else 0
    return f"{dirs} dir{'s' if dirs != 1 else ''}, {entries} from config"


def main(argv: list[str] | None = None) -> None:
    """Entry point for the ``selenium-flow`` console script."""
    try:
        loaded = config.load(argv)
    except config.ConfigError as exc:
        # A config this server cannot run on stops the boot with the reason:
        # restarted until fixed beats started on something misread (§F4.12).
        raise SystemExit(f"selenium-flow: {exc}") from None
    settings = loaded.settings
    logging.basicConfig(level=settings.log_level)
    server = SeleniumMCP(settings, sources=loaded.sources)
    log.info(
        "config=%s grid=%s auth=%s sessions=%s skill=%s flows=%s secrets=%s",
        settings.config_file or "none",
        without_userinfo(settings.grid.url),
        "on" if server.auth_token else "off",
        server.sessions.kind,
        server.skill.skill_info.name if server.skill else "off",
        server.flows.kind if server.flows else "off",
        _secrets_summary(server.secrets),
    )
    server.run(transport=settings.transport, host=settings.host, port=settings.port)


if __name__ == "__main__":
    main()
