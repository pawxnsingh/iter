from importlib.metadata import version

import setproctitle


def get_version() -> str:
    """Get the current version of Toad.

    Returns:
        str: Version string, e.g "1.2.3"
    """
    try:
        return version("iter")
    except Exception:
        return "0.0.1Unknown"
    
    
def set_process_title(title: str) -> None:
    """Set the process title.

    Args:
        title: Desired title.
    """
    try:
        import setproctitle

        setproctitle.setproctitle(title)
    except Exception:
        pass



