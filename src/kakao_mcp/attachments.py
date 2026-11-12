"""Compatibility entry point for callers using the original Python attachment API."""
from .delivery import deliver


def send_files(room, paths):
    return deliver('file', [(room, str(path)) for path in paths])
