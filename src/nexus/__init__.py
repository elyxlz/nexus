import sys


def hello() -> str:
    return "Hello from nexus!"


if not sys.platform.startswith("linux"):
    raise RuntimeError("This package is only compatible with the Linux operating systems")
