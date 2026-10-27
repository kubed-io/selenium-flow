"""The live view: the admin UI's window onto a Grid browser, over VNC.

The proxy owns the tunnel's life -- it dials the Grid, answers both RFB
handshakes itself and decides when the socket ends -- and Watch is the gate's:
what a viewer may send the browser is decided by parsing its stream into whole
messages (`live/rfb.py`), never by trusting the page to behave.

A plain layer: it imports no protocol library and nothing above it
(`tests/test_boundaries.py`).
"""
