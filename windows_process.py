from __future__ import annotations

import os
import subprocess


def hidden_process_flags(extra_flags: int = 0) -> int:
    """Return Windows flags that prevent console subprocesses from flashing onscreen."""
    if os.name != "nt":
        return extra_flags
    return extra_flags | int(getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000))
