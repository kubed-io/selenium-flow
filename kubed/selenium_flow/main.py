"""Entry point: the config, loaded from file, env and args, into a running server."""

from __future__ import annotations

import logging

from kubed.mcp.plugins import PluginError

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
    stores each physical line as its own entry; ``QuickMCP.run()`` drops those
    handlers and has it propagate, on either transport. It logs every tool's
    arguments at DEBUG (scripts, typed text, upload content), so DEBUG never
    opens it, while WARNING still quiets it.
    """
    logging.basicConfig(level=level)
    # The root's level, not ``level``: basicConfig does nothing when the root
    # already has a handler.
    floor = max(logging.INFO, logging.getLogger().getEffectiveLevel())
    for name in WIRE_LOGGERS:
        logging.getLogger(name).setLevel(floor)
    logging.getLogger("fastmcp").setLevel(floor)


def _telemetry_summary(telemetry: config.TelemetrySettings) -> str:
    """The signals on, never the address: it may carry basic-auth userinfo."""
    on = []
    if telemetry.profiles and (telemetry.profile_cpu or telemetry.profile_memory):
        on.append("profiles")
    if telemetry.otel:
        on.append("otel")
    return ", ".join(on) or "off"


def _loopback(host: str) -> bool:
    return host in ("localhost", "::1", "127.0.0.1")


def main(argv: list[str] | None = None) -> None:
    """Entry point for the ``selenium-flow`` console script."""
    try:
        loaded = config.load(argv)
        settings = loaded.settings
        configure_logging(settings.log_level)
        server = create_app(settings, sources=loaded.sources)
    except (config.ConfigError, StoreUnavailable, PluginError) as exc:
        # Restarted until fixed beats started on something misread. A PluginError is
        # a plugin/ folder kubed-mcp refuses: missing package data, a broken manifest.
        raise SystemExit(f"selenium-flow: {exc}") from None
    log.info(
        "config=%s grid=%s auth=%s oidc=%s workspaces=%s skill=%s flows=%s secrets=%s "
        "recordings=%s telemetry=%s",
        settings.config_file or "none",
        without_userinfo(settings.grid.url),
        "on" if server.auth_token else "off",
        settings.oidc.issuer or "off",
        server.workspaces.kind,
        ", ".join(server.plugin.skills) or "none",
        server.flows.kind if server.flows else "off",
        _secrets_summary(server.secrets),
        "on" if server.collector else "off",
        _telemetry_summary(settings.telemetry),
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
