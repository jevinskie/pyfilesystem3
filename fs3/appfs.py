"""Manage filesystems in platform-specific application directories.

These classes abstract away the different requirements for user data
across platforms, which vary in their conventions. They are all
subclasses of `~fs3.osfs.OSFS`.

"""
# Thanks to authors of https://pypi.org/project/appdirs

# see https://learn.microsoft.com/en-us/previous-versions/windows/it-pro/windows-vista/cc766489(v=ws.10)

import typing

import abc
from appdirs import AppDirs

from ._repr import make_repr
from .osfs import OSFS

if typing.TYPE_CHECKING:
    from typing import Optional


__all__ = [
    "UserDataFS",
    "UserConfigFS",
    "SiteDataFS",
    "SiteConfigFS",
    "UserCacheFS",
    "UserLogFS",
]


class _CopyInitMeta(abc.ABCMeta):
    """A metaclass that performs a hard copy of the `__init__`.

    This is a fix for Sphinx, which is a pain to configure in a way that
    it documents the ``__init__`` method of a class when it is inherited.
    Copying ``__init__`` makes it think it is not inherited, and let us
    share the documentation between all the `_AppFS` subclasses.

    """

    def __new__(mcls, classname, bases, cls_dict):
        cls_dict.setdefault("__init__", bases[0].__init__)
        return super(abc.ABCMeta, mcls).__new__(mcls, classname, bases, cls_dict)


class _AppFS(OSFS):
    """Abstract base class for an app FS."""

    # FIXME(@althonos): replace by ClassVar[str] once
    # https://github.com/python/mypy/pull/4718 is accepted
    # (subclass override will raise errors until then)
    app_dir = None  # type: str

    def __init__(
        self,
        appname,  # type: str
        author=None,  # type: Optional[str]
        version=None,  # type: Optional[str]
        roaming=False,  # type: bool
        create=True,  # type: bool
    ):
        # type: (...) -> None
        """Create a new application-specific filesystem.

        Arguments:
            appname (str): The name of the application.
            author (str): The name of the author (used on Windows).
            version (str): Optional version string, if a unique location
                per version of the application is required.
            roaming (bool): If `True`, use a *roaming* profile on
                Windows.
            create (bool): If `True` (the default) the directory
                will be created if it does not exist.

        """
        self.app_dirs = AppDirs(appname, author, version, roaming)
        self._create = create
        super(_AppFS, self).__init__(
            getattr(self.app_dirs, self.app_dir), create=create
        )

    def __repr__(self):
        # type: () -> str
        return make_repr(
            self.__class__.__name__,
            self.app_dirs.appname,
            author=(self.app_dirs.appauthor, None),
            version=(self.app_dirs.version, None),
            roaming=(self.app_dirs.roaming, False),
            create=(self._create, True),
        )

    def __str__(self):
        # type: () -> str
        return "<{} '{}'>".format(
            self.__class__.__name__.lower(), self.app_dirs.appname
        )


class UserDataFS(_AppFS):
    """A filesystem for per-user application data.

    May also be opened with
    ``open_fs('userdata://appname:author:version')``.

    """

    app_dir = "user_data_dir"


class UserConfigFS(_AppFS):
    """A filesystem for per-user config data.

    May also be opened with
    ``open_fs('userconf://appname:author:version')``.

    """

    app_dir = "user_config_dir"


class UserCacheFS(_AppFS):
    """A filesystem for per-user application cache data.

    May also be opened with
    ``open_fs('usercache://appname:author:version')``.

    """

    app_dir = "user_cache_dir"


class SiteDataFS(_AppFS):
    """A filesystem for application site data.

    May also be opened with
    ``open_fs('sitedata://appname:author:version')``.

    """

    app_dir = "site_data_dir"


class SiteConfigFS(_AppFS):
    """A filesystem for application config data.

    May also be opened with
    ``open_fs('siteconf://appname:author:version')``.

    """

    app_dir = "site_config_dir"


class UserLogFS(_AppFS):
    """A filesystem for per-user application log data.

    May also be opened with
    ``open_fs('userlog://appname:author:version')``.

    """

    app_dir = "user_log_dir"
