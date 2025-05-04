import sys
import shutil

if not sys.platform.startswith("linux"):
    raise RuntimeError("This package is only compatible with the Linux operating systems")

for cmd in ("screen", "script"):
    if shutil.which(cmd) is None:
        raise RuntimeError(f"Required command '{cmd}' not found in PATH. Please install it to proceed.")
