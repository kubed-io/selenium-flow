"""Entry point: the config, loaded from file, env and args, into a running server."""

from __future__ import annotations

import logging

from . import config
from .server import SeleniumMCP
from .urls import without_userinfo

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


# Selenium logs every BiDi frame and command body at DEBUG, and site data's
# frames carry cookie values, which are never logged.
WIRE_LOGGERS = (
    "selenium.webdriver.remote.websocket_connection",
    "selenium.webdriver.remote.remote_connection",
)


def quiet_the_wire() -> None:
    """Hold Selenium's wire loggers at INFO or above, whatever LOG_LEVEL is."""
    level = max(logging.INFO, logging.getLogger().getEffectiveLevel())
    for name in WIRE_LOGGERS:
        logging.getLogger(name).setLevel(level)


def _loopback(host: str) -> bool:
    return host in ("localhost", "::1", "127.0.0.1")


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
    quiet_the_wire()
    try:
        server = SeleniumMCP(settings, sources=loaded.sources)
    except config.ConfigError as exc:
        raise SystemExit(f"selenium-flow: {exc}") from None
    log.info(
        "config=%s grid=%s auth=%s oidc=%s sessions=%s skill=%s flows=%s secrets=%s "
        "recordings=%s",
        settings.config_file or "none",
        without_userinfo(settings.grid.url),
        "on" if server.auth_token else "off",
        settings.oidc.issuer or "off",
        server.workspaces.kind,
        server.skill.skill_info.name if server.skill else "off",
        server.flows.kind if server.flows else "off",
        _secrets_summary(server.secrets),
        "on" if server.collector else "off",
    )
    if settings.oidc.issuer and not settings.oidc.roles:
        log.warning(
            "oidc.roles is empty: any token from %s for %s gets in",
            settings.oidc.issuer,
            settings.oidc.audience,
        )
    if (
        not server.auth_token
        and settings.transport == "http"
        and not _loopback(settings.host)
    ):
        log.warning(
            "AUTH_TOKEN is not set: anyone who can reach %s:%s can drive every browser",
            settings.host,
            settings.port,
        )
    server.run(transport=settings.transport, host=settings.host, port=settings.port)


if __name__ == "__main__":
    main()
