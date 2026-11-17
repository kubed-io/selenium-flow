"""What a file this server handles is called.

The names an uploaded, captured or printed file gets, and the sentence a failed
capture is reported with. Bytes and strings in, strings out.
"""

from __future__ import annotations

import base64
import binascii
import logging
import mimetypes
from pathlib import PurePosixPath

log = logging.getLogger(__name__)


def _decode(content) -> bytes:
    """Base64 file content as bytes, with a legible error if it is not base64."""
    try:
        return base64.b64decode(str(content), validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ValueError(
            "content must be base64-encoded file bytes; "
            "use the multipart form of the HTTP endpoint to send a raw file"
        ) from exc


# The browser reads File.type from the file's extension, so mime_type can only
# choose one. These are where the stdlib is wrong (.xsl for application/xml) or
# silent (yaml, ndjson).
EXTENSIONS = {
    "application/json": ".json",
    "application/xml": ".xml",
    "text/xml": ".xml",
    "text/yaml": ".yaml",
    "application/yaml": ".yaml",
    "application/x-yaml": ".yaml",
    "application/x-ndjson": ".ndjson",
    "text/markdown": ".md",
    "text/csv": ".csv",
    "text/tab-separated-values": ".tsv",
    "text/plain": ".txt",
    "text/html": ".html",
}


def _extension_for(mime_type) -> str:
    """The extension that makes a browser report ``mime_type``, or ""."""
    if not mime_type:
        return ""
    key = str(mime_type).split(";")[0].strip().lower()
    return EXTENSIONS.get(key) or mimetypes.guess_extension(key) or ""


def safe_name(filename, mime_type=None, default_extension="") -> str:
    """The name the page will see for an uploaded file.

    Reduced to a basename because the caller chooses it and it is written to
    disk here. An extension is appended when there is none, since without one
    the page reports an empty File.type.
    """
    # Backslashes are folded first, so a Windows-style path is reduced too.
    raw_name = str(filename or "").replace("\\", "/")
    name = PurePosixPath(raw_name).name.strip().lstrip(".")
    if not name:
        name = "upload"
    if not PurePosixPath(name).suffix:
        name += _extension_for(mime_type) or default_extension
    return name


def _generated_name(filename, extension: str) -> str:
    """A name for bytes this server made, whose type it already knows.

    A screenshot called `chart.pdf` is still a PNG, and the file store serves
    the type its name says, so ``extension`` is appended when the name lacks it.
    """
    name = safe_name(filename, None, extension)
    if PurePosixPath(name).suffix.lower() != extension:
        name += extension
    return name


def _why_unsaved(exc: BaseException) -> str:
    """Why a capture could not be kept, without quoting the server's disk at it.

    Our own refusals (`ValueError`) are repeated. Anything else, an `OSError`
    above all, can carry a path under `DATA_DIR`, so it is reported by type and
    the detail stays in the log.
    """
    if isinstance(exc, ValueError):
        return str(exc)
    log.warning("a capture could not be kept", exc_info=exc)
    return f"the capture could not be kept ({type(exc).__name__})"
