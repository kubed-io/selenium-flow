"""The live view: the admin UI's window onto a Grid browser, over VNC.

The proxy dials the Grid, answers both RFB handshakes itself and decides when
the socket ends. What a viewer may send the browser is decided by parsing its
stream into whole messages (`rfb.py`), never by trusting the page.

It imports no protocol library and nothing above it (`tests/test_boundaries.py`).
"""
