"""Recordings: the Grid films a browser; this package files the video.

The Grid's recorder writes the MP4 and the operator's transport drops it in the
inbox (``recording.dir``). Nothing here talks to the Grid's recorder or moves a
file across machines (recordings spec, ruling 1). No protocol imports and no
``selenium``: the boundary test holds this package to it.
"""
