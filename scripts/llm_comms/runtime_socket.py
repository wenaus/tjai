"""Standard-library validation shared by runtime connections and startup hooks."""

import os
from pathlib import Path
import stat


def private_socket_path(path):
    """Accept direct sockets and Codex daemon links without trusting public links."""
    path = Path(path)
    info = path.lstat()
    if stat.S_ISLNK(info.st_mode):
        parent = path.parent.stat()
        if (info.st_uid != os.getuid() or parent.st_uid != os.getuid()
                or parent.st_mode & 0o077):
            raise ValueError("The app-server socket link must be private to its owner")
        path = path.resolve(strict=True)
        parent = path.parent.stat()
        if parent.st_uid != os.getuid() or parent.st_mode & 0o077:
            raise ValueError("The app-server socket directory must be private to its owner")
        info = path.lstat()
    if not stat.S_ISSOCK(info.st_mode) or info.st_uid != os.getuid():
        raise ValueError("Select an app-server socket owned by the current user")
    if info.st_mode & 0o077:
        raise ValueError("The app-server socket must be private to its owner")
    return path
