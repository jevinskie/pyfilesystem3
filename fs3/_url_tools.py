import platform
import re
import urllib.request

_WINDOWS_PLATFORM = platform.system() == "Windows"


def url_quote(path_snippet):
    # type: (str) -> str
    """Quote a URL without quoting the Windows drive letter, if any.

    On Windows, it will separate drive letter and quote Windows
    path alone. No magic on Unix-like path, just pythonic
    `~urllib.request.pathname2url`.

    Arguments:
       path_snippet (str): a file path, relative or absolute.

    """
    if _WINDOWS_PLATFORM and _has_drive_letter(path_snippet):
        drive_letter, path = path_snippet.split(":", 1)
        path = urllib.request.pathname2url(path)
        path_snippet = "{}:{}".format(drive_letter, path)
    else:
        path_snippet = urllib.request.pathname2url(path_snippet)
    return path_snippet


def _has_drive_letter(path_snippet):
    # type: (str) -> bool
    """Check whether a path contains a drive letter.

    Arguments:
       path_snippet (str): a file path, relative or absolute.

    Example:
        >>> _has_drive_letter("D:/Data")
        True
        >>> _has_drive_letter(r"C:\\System32\\ test")
        True
        >>> _has_drive_letter("/tmp/abc:test")
        False

    """
    windows_drive_pattern = ".:[/\\\\].*$"
    return re.match(windows_drive_pattern, path_snippet) is not None
