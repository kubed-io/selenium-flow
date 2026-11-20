"""Entry point: the config, loaded from file, env and args, into a running server."""

from __future__ import annotations

import logging

from . import config
from .server import create_app
from .urls import without_userinfo
from .workspace.store import StoreUnavailable

log = logging.getLogger(__name__)


def _secrets_summary(catalogue) -> str:
    """"off", or how many directories and how many config entries feed it."""
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


def configure_logging(level: str) -> None:
    """The root handler at ``level``, with FastMCP's and Selenium's wire
    loggers held at the higher of INFO and the root's level.

    FastMCP configures its own logger at import: Rich handlers on stderr and no
    propagation. In a pod there is no TTY, so Rich wraps at 80 columns, and Loki
    stores each physical line as its own entry, so its handlers go and it
    propagates. It logs every tool's arguments at DEBUG (scripts, typed text,
    upload content), so DEBUG never opens it, while WARNING still quiets it.
    Filters on its child loggers stay, as they belong to the logger and not to
    a handler.
    """
    logging.basicConfig(level=level)
    # The root's level, not ``level``: basicConfig does nothing when the root
    # already has a handler.
    floor = max(logging.INFO, logging.getLogger().getEffectiveLevel())
    for name in WIRE_LOGGERS:
        logging.getLogger(name).setLevel(floor)
    fastmcp = logging.getLogger("fastmcp")
    for handler in list(fastmcp.handlers):
        fastmcp.removeHandler(handler)
    fastmcp.propagate = True
    fastmcp.setLevel(floor)


def _loopback(host: str) -> bool:
    return host in ("localhost", "::1", "127.0.0.1")


def main(argv: list[str] | None = None) -> None:
    """Entry point for the ``selenium-flow`` console script."""
    try:
        loaded = config.load(argv)
        settings = loaded.settings
        configure_logging(settings.log_level)
        server = create_app(settings, sources=loaded.sources)
    except (config.ConfigError, StoreUnavailable) as exc:
        # Restarted until fixed beats started on something misread.
        raise SystemExit(f"selenium-flow: {exc}") from None
    log.info(
        "config=%s grid=%s auth=%s oidc=%s workspaces=%s skill=%s flows=%s secrets=%s "
        "recordings=%s",
        settings.config_file or "none",
        without_userinfo(settings.grid.url),
        "on" if server.auth_token else "off",
        settings.oidc.issuer or "off",
        server.workspaces.kind,
        server.skill.name
        if server.skill
        else ("not loaded" if settings.mcp.skill else "off"),
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
    server.run()


if __name__ == "__main__":
    main()
