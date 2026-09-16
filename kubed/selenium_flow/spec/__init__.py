"""The OpenAPI document: its builder, and the schemas it assembles.

Re-exported here so a caller says ``from .spec import build_spec`` without
caring which half a name lives in — the split is about reading the source, not
about the surface it presents.
"""

from __future__ import annotations

from .builder import DESCRIPTION, PLACEHOLDER_VERSION, build_spec
from .schemas import (
    _FILE_OPERATIONS,
    _FLOW_OPERATIONS,
    _SESSION,
    _WRITE_SESSION,
    ERROR,
    FILE_SCHEMAS,
    FLOW_SCHEMAS,
    FLOW_STEP,
    HEALTH,
    INFO,
    PAGE_STATE,
    READY,
    RESPONSES,
    SESSION_PARAMETERS,
    STARTED,
)

__all__ = [
    "DESCRIPTION",
    "ERROR",
    "FILE_SCHEMAS",
    "FLOW_SCHEMAS",
    "FLOW_STEP",
    "HEALTH",
    "INFO",
    "PAGE_STATE",
    "PLACEHOLDER_VERSION",
    "READY",
    "RESPONSES",
    "SESSION_PARAMETERS",
    "STARTED",
    "_FILE_OPERATIONS",
    "_FLOW_OPERATIONS",
    "_SESSION",
    "_WRITE_SESSION",
    "build_spec",
]
