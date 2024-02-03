"""PyFilesystem base class.

The filesystem base class is common to all filesystems. If you
familiarize yourself with this (rather straightforward) API, you
can work with any of the supported filesystems.

"""

import typing

import abc
import hashlib
import itertools
import os
import threading
import time
import warnings
from contextlib import closing
from functools import partial, wraps

from . import copy, errors, fsencode, glob, iotools, tools, walk, wildcard
from .copy import copy_modified_time
from .glob import BoundGlobber
from .mode import validate_open_mode
from .path import abspath, isbase, join, normpath
from .time import datetime_to_epoch
from .walk import Walker

if typing.TYPE_CHECKING:
    from collections.abc import Callable, Collection, Iterable, Iterator, Mapping
    from typing import IO, Any, BinaryIO, Optional, Union

    from datetime import datetime
    from threading import RLock
    from types import TracebackType

    from .enums import ResourceType
    from .info import Info, RawInfo
    from .permissions import Permissions
    from .subfs import SubFS
    from .walk import BoundWalker

    _F = typing.TypeVar("_F", bound="FS")
    _T = typing.TypeVar("_T", bound="FS")
    _OpendirFactory = Callable[[_T, str], SubFS[_T]]


__all__ = ["FS"]


def _new_name(method, old_name):
    """Return a method with a deprecation warning."""
    # Looks suspiciously like a decorator, but isn't!

    @wraps(method)
    def _method(*args, **kwargs):
        warnings.warn(
            "method '{}' has been deprecated, please rename to '{}'".format(
                old_name, method.__name__
            ),
            DeprecationWarning,
        )
        return method(*args, **kwargs)

    deprecated_msg = """
        Note:
            .. deprecated:: 2.2.0
                Please use `~{}`
""".format(
        method.__name__
    )
    if getattr(_method, "__doc__", None) is not None:
        _method.__doc__ += deprecated_msg

    return _method


class FS:
    """Base class for FS objects."""

    # This is the "standard" meta namespace.
    _meta = {}  # type: dict[str, Union[str, int, bool, None]]

    # most FS will use default walking algorithms
    walker_class = Walker

    # default to SubFS, used by opendir and should be returned by makedir(s)
    subfs_class = None

    def __init__(self, *args, **kwargs):
        # type: (...) -> None
        """Create a filesystem. See help(type(self)) for accurate signature."""
        self._closed = False
        self._lock = threading.RLock()
        super().__init__(*args, **kwargs)

    def __del__(self):
        """Auto-close the filesystem on exit."""
        self.close()

    def __enter__(self):
        # type: (...) -> FS
        """Allow use of filesystem as a context manager."""
        return self

    def __exit__(
        self,
        exc_type,  # type: Optional[type[BaseException]]
        exc_value,  # type: Optional[BaseException]
        traceback,  # type: Optional[TracebackType]
    ):
        # type: (...) -> None
        """Close filesystem on exit."""
        self.close()

    @property
    def glob(self):
        """`~fs3.glob.BoundGlobber`: a globber object.."""
        return BoundGlobber(self)

    @property
    def walk(self):
        # type: (_F) -> BoundWalker[_F]
        """`~fs3.walk.BoundWalker`: a walker bound to this filesystem."""
        return self.walker_class.bind(self)

    # ---------------------------------------------------------------- #
    # Required methods                                                 #
    # Filesystems must implement these methods.                        #
    # ---------------------------------------------------------------- #

    @abc.abstractmethod
    def getinfo(self, path, namespaces=None):
        # type: (str, Optional[Collection[str]]) -> Info
        """Get information about a resource on a filesystem.

        Arguments:
            path (str): A path to a resource on the filesystem.
            namespaces (list, optional): Info namespaces to query. The
                `"basic"` namespace is alway included in the returned
                info, whatever the value of `namespaces` may be.

        Returns:
            ~fs3.info.Info: resource information object.

        Raises:
            ~fs3.errors.ResourceNotFound: If ``path`` does not exist.

        For more information regarding resource information, see :ref:`info`.

        """

    @abc.abstractmethod
    def listdir(self, path):
        # type: (str) -> list[str]
        """Get a list of the resource names in a directory.

        This method will return a list of the resources in a directory.
        A *resource* is a file, directory, or one of the other types
        defined in `~fs3.enums.ResourceType`.

        Arguments:
            path (str): A path to a directory on the filesystem

        Returns:
            list: list of names, relative to ``path``.

        Raises:
            ~fs3.errors.DirectoryExpected: If ``path`` is not a directory.
            ~fs3.errors.ResourceNotFound: If ``path`` does not exist.

        """

    @abc.abstractmethod
    def makedir(
        self,
        path,  # type: str
        permissions=None,  # type: Optional[Permissions]
        recreate=False,  # type: bool
    ):
        # type: (...) -> SubFS[FS]
        """Make a directory.

        Arguments:
            path (str): Path to directory from root.
            permissions (~fs3.permissions.Permissions, optional): a
                `Permissions` instance, or `None` to use default.
            recreate (bool): Set to `True` to avoid raising an error if
                the directory already exists (defaults to `False`).

        Returns:
            ~fs3.subfs.SubFS: a filesystem whose root is the new directory.

        Raises:
            ~fs3.errors.DirectoryExists: If the path already exists.
            ~fs3.errors.ResourceNotFound: If the path is not found.

        """

    @abc.abstractmethod
    def openbin(
        self,
        path,  # type: str
        mode="r",  # type: str
        buffering=-1,  # type: int
        **options  # type: Any
    ):
        # type: (...) -> BinaryIO
        """Open a binary file-like object.

        Arguments:
            path (str): A path on the filesystem.
            mode (str): Mode to open file (must be a valid non-text mode,
                defaults to *r*). Since this method only opens binary files,
                the ``b`` in the mode string is implied.
            buffering (int): Buffering policy (-1 to use default buffering,
                0 to disable buffering, or any positive integer to indicate
                a buffer size).
            **options: keyword arguments for any additional information
                required by the filesystem (if any).

        Returns:
            io.IOBase: a *file-like* object.

        Raises:
            ~fs3.errors.FileExpected: If ``path`` exists and is not a file.
            ~fs3.errors.FileExists: If the ``path`` exists, and
                *exclusive mode* is specified (``x`` in the mode).
            ~fs3.errors.ResourceNotFound: If ``path`` does not exist and
                ``mode`` does not imply creating the file, or if any
                ancestor of ``path`` does not exist.

        """

    @abc.abstractmethod
    def remove(self, path):
        # type: (str) -> None
        """Remove a file from the filesystem.

        Arguments:
            path (str): Path of the file to remove.

        Raises:
           ~fs3.errors.FileExpected: If the path is a directory.
           ~fs3.errors.ResourceNotFound: If the path does not exist.

        """

    @abc.abstractmethod
    def removedir(self, path):
        # type: (str) -> None
        """Remove a directory from the filesystem.

        Arguments:
            path (str): Path of the directory to remove.

        Raises:
           ~fs3.errors.DirectoryNotEmpty: If the directory is not empty (
                see `~fs3.base.FS.removetree` for a way to remove the
                directory contents).
           ~fs3.errors.DirectoryExpected: If the path does not refer to
                a directory.
           ~fs3.errors.ResourceNotFound: If no resource exists at the
                given path.
           ~fs3.errors.RemoveRootError: If an attempt is made to remove
                the root directory (i.e. ``'/'``)

        """

    @abc.abstractmethod
    def setinfo(self, path, info):
        # type: (str, RawInfo) -> None
        """Set info on a resource.

        This method is the complement to `~fs3.base.FS.getinfo`
        and is used to set info values on a resource.

        Arguments:
            path (str): Path to a resource on the filesystem.
            info (dict): dictionary of resource info.

        Raises:
           ~fs3.errors.ResourceNotFound: If ``path`` does not exist
                on the filesystem

        The ``info`` dict should be in the same format as the raw
        info returned by ``getinfo(file).raw``.

        Example:
            >>> details_info = {"details": {
            ...     "modified": time.time()
            ... }}
            >>> my_fs.setinfo('file.txt', details_info)

        """

    # ---------------------------------------------------------------- #
    # Optional methods                                                 #
    # Filesystems *may* implement these methods.                       #
    # ---------------------------------------------------------------- #

    def appendbytes(self, path, data):
        # type: (str, bytes) -> None
        # FIXME(@althonos): accept bytearray and memoryview as well ?
        """Append bytes to the end of a file, creating it if needed.

        Arguments:
            path (str): Path to a file.
            data (bytes): Bytes to append.

        Raises:
            TypeError: If ``data`` is not a `bytes` instance.
            ~fs3.errors.ResourceNotFound: If a parent directory of
                ``path`` does not exist.

        """
        if not isinstance(data, bytes):
            raise TypeError("must be bytes")
        with self._lock:
            with self.open(path, "ab") as append_file:
                append_file.write(data)

    def appendtext(
        self,
        path,  # type: str
        text,  # type: str
        encoding="utf-8",  # type: str
        errors=None,  # type: Optional[str]
        newline="",  # type: str
    ):
        # type: (...) -> None
        """Append text to the end of a file, creating it if needed.

        Arguments:
            path (str): Path to a file.
            text (str): str to append.
            encoding (str): Encoding for text files (defaults to ``utf-8``).
            errors (str, optional): What to do with unicode decode errors
                (see `codecs` module for more information).
            newline (str): Newline parameter.

        Raises:
            TypeError: if ``text`` is not an unicode string.
            ~fs3.errors.ResourceNotFound: if a parent directory of
                ``path`` does not exist.

        """
        if not isinstance(text, str):
            raise TypeError("must be unicode string")
        with self._lock:
            with self.open(
                path, "at", encoding=encoding, errors=errors, newline=newline
            ) as append_file:
                append_file.write(text)

    def close(self):
        # type: () -> None
        """Close the filesystem and release any resources.

        It is important to call this method when you have finished
        working with the filesystem. Some filesystems may not finalize
        changes until they are closed (archives for example). You may
        call this method explicitly (it is safe to call close multiple
        times), or you can use the filesystem as a context manager to
        automatically close.

        Example:
            >>> with OSFS('~/Desktop') as desktop_fs:
            ...    desktop_fs.writetext(
            ...        'note.txt',
            ...        "Don't forget to tape Game of Thrones"
            ...    )

        If you attempt to use a filesystem that has been closed, a
        `~fs3.errors.FilesystemClosed` exception will be thrown.

        """
        self._closed = True

    def copy(
        self,
        src_path,  # type: str
        dst_path,  # type: str
        overwrite=False,  # type: bool
        preserve_time=False,  # type: bool
    ):
        # type: (...) -> None
        """Copy file contents from ``src_path`` to ``dst_path``.

        Arguments:
            src_path (str): Path of source file.
            dst_path (str): Path to destination file.
            overwrite (bool): If `True`, overwrite the destination file
                if it exists (defaults to `False`).
            preserve_time (bool): If `True`, try to preserve mtime of the
                resource (defaults to `False`).

        Raises:
            ~fs3.errors.DestinationExists: If ``dst_path`` exists,
                and ``overwrite`` is `False`.
            ~fs3.errors.ResourceNotFound: If a parent directory of
                ``dst_path`` does not exist.
            ~fs3.errors.FileExpected: If ``src_path`` is not a file.

        """
        with self._lock:
            _src_path = self.validatepath(src_path)
            _dst_path = self.validatepath(dst_path)
            if not overwrite and self.exists(_dst_path):
                raise errors.DestinationExists(dst_path)
            if _src_path == _dst_path:
                raise errors.IllegalDestination(dst_path)
            with closing(self.open(_src_path, "rb")) as read_file:
                # FIXME(@althonos): typing complains because open return IO
                self.upload(_dst_path, read_file)  # type: ignore
            if preserve_time:
                copy_modified_time(self, _src_path, self, _dst_path)

    def copydir(
        self,
        src_path,  # type: str
        dst_path,  # type: str
        create=False,  # type: bool
        preserve_time=False,  # type: bool
    ):
        # type: (...) -> None
        """Copy the contents of ``src_path`` to ``dst_path``.

        Arguments:
            src_path (str): Path of source directory.
            dst_path (str): Path to destination directory.
            create (bool): If `True`, then ``dst_path`` will be created
                if it doesn't exist already (defaults to `False`).
            preserve_time (bool): If `True`, try to preserve mtime of the
                resource (defaults to `False`).

        Raises:
            ~fs3.errors.ResourceNotFound: If the ``dst_path``
                does not exist, and ``create`` is not `True`.
            ~fs3.errors.DirectoryExpected: If ``src_path`` is not a
                directory.

        """
        with self._lock:
            _src_path = self.validatepath(src_path)
            _dst_path = self.validatepath(dst_path)
            if isbase(_src_path, _dst_path):
                raise errors.IllegalDestination(dst_path)
            if not create and not self.exists(_dst_path):
                raise errors.ResourceNotFound(dst_path)
            if not self.getinfo(_src_path).is_dir:
                raise errors.DirectoryExpected(src_path)
            copy.copy_dir(self, _src_path, self, _dst_path, preserve_time=preserve_time)

    def create(self, path, wipe=False):
        # type: (str, bool) -> bool
        """Create an empty file.

        The default behavior is to create a new file if one doesn't
        already exist. If ``wipe`` is `True`, any existing file will
        be truncated.

        Arguments:
            path (str): Path to a new file in the filesystem.
            wipe (bool): If `True`, truncate any existing
                file to 0 bytes (defaults to `False`).

        Returns:
            bool: `True` if a new file had to be created.

        """
        with self._lock:
            if not wipe and self.exists(path):
                return False
            with closing(self.open(path, "wb")):
                pass
            return True

    def desc(self, path):
        # type: (str) -> str
        """Return a short descriptive text regarding a path.

        Arguments:
            path (str): A path to a resource on the filesystem.

        Returns:
            str: a short description of the path.

        Raises:
            ~fs3.errors.ResourceNotFound: If ``path`` does not exist.

        """
        if not self.exists(path):
            raise errors.ResourceNotFound(path)
        try:
            syspath = self.getsyspath(path)
        except (errors.ResourceNotFound, errors.NoSysPath):
            return "{} on {}".format(path, self)
        else:
            return syspath

    def exists(self, path):
        # type: (str) -> bool
        """Check if a path maps to a resource.

        Arguments:
            path (str): Path to a resource.

        Returns:
            bool: `True` if a resource exists at the given path.

        """
        try:
            self.getinfo(path)
        except errors.ResourceNotFound:
            return False
        else:
            return True

    def filterdir(
        self,
        path,  # type: str
        files=None,  # type: Optional[Iterable[str]]
        dirs=None,  # type: Optional[Iterable[str]]
        exclude_dirs=None,  # type: Optional[Iterable[str]]
        exclude_files=None,  # type: Optional[Iterable[str]]
        namespaces=None,  # type: Optional[Collection[str]]
        page=None,  # type: Optional[tuple[int, int]]
    ):
        # type: (...) -> Iterator[Info]
        """Get an iterator of resource info, filtered by patterns.

        This method enhances `~fs3.base.FS.scandir` with additional
        filtering functionality.

        Arguments:
            path (str): A path to a directory on the filesystem.
            files (list, optional): A list of UNIX shell-style patterns
                to filter file names, e.g. ``['*.py']``.
            dirs (list, optional): A list of UNIX shell-style patterns
                to filter directory names.
            exclude_dirs (list, optional): A list of patterns used
                to exclude directories.
            exclude_files (list, optional): A list of patterns used
                to exclude files.
            namespaces (list, optional): A list of namespaces to include
                in the resource information, e.g. ``['basic', 'access']``.
            page (tuple, optional): May be a tuple of ``(<start>, <end>)``
                indexes to return an iterator of a subset of the resource
                info, or `None` to iterate over the entire directory.
                Paging a directory scan may be necessary for very large
                directories.

        Returns:
            ~collections.abc.Iterator: an iterator of `Info` objects.

        """
        resources = self.scandir(path, namespaces=namespaces)
        filters = []

        def match_dir(patterns, info):
            # type: (Optional[Iterable[str]], Info) -> bool
            """Pattern match info.name."""
            return info.is_file or self.match(patterns, info.name)

        def match_file(patterns, info):
            # type: (Optional[Iterable[str]], Info) -> bool
            """Pattern match info.name."""
            return info.is_dir or self.match(patterns, info.name)

        def exclude_dir(patterns, info):
            # type: (Optional[Iterable[str]], Info) -> bool
            """Pattern match info.name."""
            return info.is_file or not self.match(patterns, info.name)

        def exclude_file(patterns, info):
            # type: (Optional[Iterable[str]], Info) -> bool
            """Pattern match info.name."""
            return info.is_dir or not self.match(patterns, info.name)

        if files:
            filters.append(partial(match_file, files))
        if dirs:
            filters.append(partial(match_dir, dirs))
        if exclude_dirs:
            filters.append(partial(exclude_dir, exclude_dirs))
        if exclude_files:
            filters.append(partial(exclude_file, exclude_files))

        if filters:
            resources = (
                info for info in resources if all(_filter(info) for _filter in filters)
            )

        iter_info = iter(resources)
        if page is not None:
            start, end = page
            iter_info = itertools.islice(iter_info, start, end)
        return iter_info

    def readbytes(self, path):
        # type: (str) -> bytes
        """Get the contents of a file as bytes.

        Arguments:
            path (str): A path to a readable file on the filesystem.

        Returns:
            bytes: the file contents.

        Raises:
            ~fs3.errors.FileExpected: if ``path`` exists but is not a file.
            ~fs3.errors.ResourceNotFound: if ``path`` does not exist.

        """
        with closing(self.open(path, mode="rb")) as read_file:
            contents = read_file.read()
        return contents

    getbytes = _new_name(readbytes, "getbytes")

    def download(self, path, file, chunk_size=None, **options):
        # type: (str, BinaryIO, Optional[int], **Any) -> None
        """Copy a file from the filesystem to a file-like object.

        This may be more efficient that opening and copying files
        manually if the filesystem supplies an optimized method.

        Note that the file object ``file`` will *not* be closed by this
        method. Take care to close it after this method completes
        (ideally with a context manager).

        Arguments:
            path (str): Path to a resource.
            file (file-like): A file-like object open for writing in
                binary mode.
            chunk_size (int, optional): Number of bytes to read at a
                time, if a simple copy is used, or `None` to use
                sensible default.
            **options: Implementation specific options required to open
                the source file.

        Example:
            >>> with open('starwars.mov', 'wb') as write_file:
            ...     my_fs.download('/Videos/starwars.mov', write_file)

        Raises:
            ~fs3.errors.ResourceNotFound: if ``path`` does not exist.

        """
        with self._lock:
            with self.openbin(path, **options) as read_file:
                tools.copy_file_data(read_file, file, chunk_size=chunk_size)

    getfile = _new_name(download, "getfile")

    def readtext(
        self,
        path,  # type: str
        encoding=None,  # type: Optional[str]
        errors=None,  # type: Optional[str]
        newline="",  # type: str
    ):
        # type: (...) -> str
        """Get the contents of a file as a string.

        Arguments:
            path (str): A path to a readable file on the filesystem.
            encoding (str, optional): Encoding to use when reading contents
                in text mode (defaults to `None`, reading in binary mode).
            errors (str, optional): Unicode errors parameter.
            newline (str): Newlines parameter.

        Returns:
            str: file contents.

        Raises:
            ~fs3.errors.ResourceNotFound: If ``path`` does not exist.

        """
        with closing(
            self.open(
                path, mode="rt", encoding=encoding, errors=errors, newline=newline
            )
        ) as read_file:
            contents = read_file.read()
        return contents


    def getmodified(self, path):
        # type: (str) -> Optional[datetime]
        """Get the timestamp of the last modifying access of a resource.

        Arguments:
            path (str): A path to a resource.

        Returns:
            datetime: The timestamp of the last modification.

        The *modified timestamp* of a file is the point in time
        that the file was last changed. Depending on the file system,
        it might only have limited accuracy.

        """
        return self.getinfo(path, namespaces=["details"]).modified

    def getmeta(self, namespace="standard"):
        # type: (str) -> Mapping[str, object]
        """Get meta information regarding a filesystem.

        Arguments:
            namespace (str): The meta namespace (defaults
                to ``"standard"``).

        Returns:
            dict: the meta information.

        Meta information is associated with a *namespace* which may be
        specified with the ``namespace`` parameter. The default namespace,
        ``"standard"``, contains common information regarding the
        filesystem's capabilities. Some filesystems may provide other
        namespaces which expose less common or implementation specific
        information. If a requested namespace is not supported by
        a filesystem, then an empty dictionary will be returned.

        The ``"standard"`` namespace supports the following keys:

        =================== ============================================
        key                 Description
        ------------------- --------------------------------------------
        case_insensitive    `True` if this filesystem is case
                            insensitive.
        invalid_path_chars  A string containing the characters that
                            may not be used on this filesystem.
        max_path_length     Maximum number of characters permitted in
                            a path, or `None` for no limit.
        max_sys_path_length Maximum number of characters permitted in
                            a sys path, or `None` for no limit.
        network             `True` if this filesystem requires a
                            network.
        read_only           `True` if this filesystem is read only.
        supports_rename     `True` if this filesystem supports an
                            `os.rename` operation.
        =================== ============================================

        Most builtin filesystems will provide all these keys, and third-
        party filesystems should do so whenever possible, but a key may
        not be present if there is no way to know the value.

        Note:
            Meta information is constant for the lifetime of the
            filesystem, and may be cached.

        """
        if namespace == "standard":
            meta = self._meta.copy()
        else:
            meta = {}
        return meta

    def getsize(self, path):
        # type: (str) -> int
        """Get the size (in bytes) of a resource.

        Arguments:
            path (str): A path to a resource.

        Returns:
            int: the *size* of the resource.

        Raises:
            ~fs3.errors.ResourceNotFound: if ``path`` does not exist.

        The *size* of a file is the total number of readable bytes,
        which may not reflect the exact number of bytes of reserved
        disk space (or other storage medium).

        The size of a directory is the number of bytes of overhead
        use to store the directory entry.

        """
        size = self.getdetails(path).size
        return size

    def getsyspath(self, path):
        # type: (str) -> str
        """Get the *system path* of a resource.

        Arguments:
            path (str): A path on the filesystem.

        Returns:
            str: the *system path* of the resource, if any.

        Raises:
            ~fs3.errors.NoSysPath: If there is no corresponding system path.

        A system path is one recognized by the OS, that may be used
        outside of PyFilesystem (in an application or a shell for
        example). This method will get the corresponding system path
        that would be referenced by ``path``.

        Not all filesystems have associated system paths. Network and
        memory based filesystems, for example, may not physically store
        data anywhere the OS knows about. It is also possible for some
        paths to have a system path, whereas others don't.

        This method will always return a str.
        See `~getospath` if you need to encode the path as bytes.

        If ``path`` doesn't have a system path, a `~fs3.errors.NoSysPath`
        exception will be thrown.

        Note:
            A filesystem may return a system path even if no
            resource is referenced by that path -- as long as it can
            be certain what that system path would be.

        """
        raise errors.NoSysPath(path=path)

    def getospath(self, path):
        # type: (str) -> bytes
        """Get the *system path* to a resource, in the OS' prefered encoding.

        Arguments:
            path (str): A path on the filesystem.

        Returns:
            str: the *system path* of the resource, if any.

        Raises:
            ~fs3.errors.NoSysPath: If there is no corresponding system path.

        This method takes the output of `~getsyspath` and encodes it to
        the filesystem's prefered encoding. In Python3 this step is
        not required, as the `os` module will do it automatically.

        Note:
            If you want your code to work in Python2.7 and Python3 then
            use this method if you want to work with the OS filesystem
            outside of the OSFS interface.

        """
        syspath = self.getsyspath(path)
        ospath = fsencode(syspath)
        return ospath

    def gettype(self, path):
        # type: (str) -> ResourceType
        """Get the type of a resource.

        Arguments:
            path (str): A path on the filesystem.

        Returns:
            ~fs3.enums.ResourceType: the type of the resource.

        Raises:
            ~fs3.errors.ResourceNotFound: if ``path`` does not exist.

        A type of a resource is an integer that identifies the what
        the resource references. The standard type integers may be one
        of the values in the `~fs3.enums.ResourceType` enumerations.

        The most common resource types, supported by virtually all
        filesystems are ``directory`` (1) and ``file`` (2), but the
        following types are also possible:

        ===================   ======
        ResourceType          value
        -------------------   ------
        unknown               0
        directory             1
        file                  2
        character             3
        block_special_file    4
        fifo                  5
        socket                6
        symlink               7
        ===================   ======

        Standard resource types are positive integers, negative values
        are reserved for implementation specific resource types.

        """
        resource_type = self.getdetails(path).type
        return resource_type

    def geturl(self, path, purpose="download"):
        # type: (str, str) -> str
        """Get the URL to a given resource.

        Arguments:
            path (str): A path on the filesystem
            purpose (str): A short string that indicates which URL
                to retrieve for the given path (if there is more than
                one). The default is ``'download'``, which should return
                a URL that serves the file. Other filesystems may support
                other values for ``purpose``: for instance, `OSFS` supports
                ``'fs'``, which returns a FS URL (see :ref:`fs-urls`).

        Returns:
            str: a URL.

        Raises:
            ~fs3.errors.NoURL: If the path does not map to a URL.

        """
        raise errors.NoURL(path, purpose)

    def hassyspath(self, path):
        # type: (str) -> bool
        """Check if a path maps to a system path.

        Arguments:
            path (str): A path on the filesystem.

        Returns:
            bool: `True` if the resource at ``path`` has a *syspath*.

        """
        has_sys_path = True
        try:
            self.getsyspath(path)
        except errors.NoSysPath:
            has_sys_path = False
        return has_sys_path

    def hasurl(self, path, purpose="download"):
        # type: (str, str) -> bool
        """Check if a path has a corresponding URL.

        Arguments:
            path (str): A path on the filesystem.
            purpose (str): A purpose parameter, as given in
                `~fs3.base.FS.geturl`.

        Returns:
            bool: `True` if an URL for the given purpose exists.

        """
        has_url = True
        try:
            self.geturl(path, purpose=purpose)
        except errors.NoURL:
            has_url = False
        return has_url

    def isclosed(self):
        # type: () -> bool
        """Check if the filesystem is closed."""
        return getattr(self, "_closed", False)

    def isdir(self, path):
        # type: (str) -> bool
        """Check if a path maps to an existing directory.

        Arguments:
            path (str): A path on the filesystem.

        Returns:
            bool: `True` if ``path`` maps to a directory.

        """
        try:
            return self.getinfo(path).is_dir
        except errors.ResourceNotFound:
            return False

    def isempty(self, path):
        # type: (str) -> bool
        """Check if a directory is empty.

        A directory is considered empty when it does not contain
        any file or any directory.

        Arguments:
            path (str): A path to a directory on the filesystem.

        Returns:
            bool: `True` if the directory is empty.

        Raises:
            errors.DirectoryExpected: If ``path`` is not a directory.
            errors.ResourceNotFound: If ``path`` does not exist.

        """
        return next(iter(self.scandir(path)), None) is None

    def isfile(self, path):
        # type: (str) -> bool
        """Check if a path maps to an existing file.

        Arguments:
            path (str): A path on the filesystem.

        Returns:
            bool: `True` if ``path`` maps to a file.

        """
        try:
            return not self.getinfo(path).is_dir
        except errors.ResourceNotFound:
            return False

    def islink(self, path):
        # type: (str) -> bool
        """Check if a path maps to a symlink.

        Arguments:
            path (str): A path on the filesystem.

        Returns:
            bool: `True` if ``path`` maps to a symlink.

        """
        self.getinfo(path)
        return False

    def lock(self):
        # type: () -> RLock
        """Get a context manager that *locks* the filesystem.

        Locking a filesystem gives a thread exclusive access to it.
        Other threads will block until the threads with the lock has
        left the context manager.

        Returns:
            threading.RLock: a lock specific to the filesystem instance.

        Example:
            >>> with my_fs.lock():  # May block
            ...    # code here has exclusive access to the filesystem
            ...    pass

        It is a good idea to put a lock around any operations that you
        would like to be *atomic*. For instance if you are copying
        files, and you don't want another thread to delete or modify
        anything while the copy is in progress.

        Locking with this method is only required for code that calls
        multiple filesystem methods. Individual methods are thread safe
        already, and don't need to be locked.

        Note:
            This only locks at the Python level. There is nothing to
            prevent other processes from modifying the filesystem
            outside of the filesystem instance.

        """
        return self._lock

    def movedir(self, src_path, dst_path, create=False, preserve_time=False):
        # type: (str, str, bool, bool) -> None
        """Move directory ``src_path`` to ``dst_path``.

        Arguments:
            src_path (str): Path of source directory on the filesystem.
            dst_path (str): Path to destination directory.
            create (bool): If `True`, then ``dst_path`` will be created
                if it doesn't exist already (defaults to `False`).
            preserve_time (bool): If `True`, try to preserve mtime of the
                resources (defaults to `False`).

        Raises:
            ~fs3.errors.ResourceNotFound: if ``dst_path`` does not exist,
                and ``create`` is `False`.
            ~fs3.errors.DirectoryExpected: if ``src_path`` or one of its
                ancestors is not a directory.

        """
        from .move import move_dir

        with self._lock:
            _src_path = self.validatepath(src_path)
            _dst_path = self.validatepath(dst_path)
            if _src_path == _dst_path:
                return
            if isbase(_src_path, _dst_path):
                raise errors.IllegalDestination(dst_path)
            if not create and not self.exists(dst_path):
                raise errors.ResourceNotFound(dst_path)
            move_dir(self, src_path, self, dst_path, preserve_time=preserve_time)

    def makedirs(
        self,
        path,  # type: str
        permissions=None,  # type: Optional[Permissions]
        recreate=False,  # type: bool
    ):
        # type: (...) -> SubFS[FS]
        """Make a directory, and any missing intermediate directories.

        Arguments:
            path (str): Path to directory from root.
            permissions (~fs3.permissions.Permissions, optional): Initial
                permissions, or `None` to use defaults.
            recreate (bool):  If `False` (the default), attempting to
                create an existing directory will raise an error. Set
                to `True` to ignore existing directories.

        Returns:
            ~fs3.subfs.SubFS: A sub-directory filesystem.

        Raises:
            ~fs3.errors.DirectoryExists: if the path is already
                a directory, and ``recreate`` is `False`.
            ~fs3.errors.DirectoryExpected: if one of the ancestors
                in the path is not a directory.

        """
        self.check()
        with self._lock:
            dir_paths = tools.get_intermediate_dirs(self, path)
            for dir_path in dir_paths:
                try:
                    self.makedir(dir_path, permissions=permissions)
                except errors.DirectoryExists:
                    if not recreate:
                        raise
            try:
                self.makedir(path, permissions=permissions)
            except errors.DirectoryExists:
                if not recreate:
                    raise
            return self.opendir(path)

    def move(self, src_path, dst_path, overwrite=False, preserve_time=False):
        # type: (str, str, bool, bool) -> None
        """Move a file from ``src_path`` to ``dst_path``.

        Arguments:
            src_path (str): A path on the filesystem to move.
            dst_path (str): A path on the filesystem where the source
                file will be written to.
            overwrite (bool): If `True`, destination path will be
                overwritten if it exists.
            preserve_time (bool): If `True`, try to preserve mtime of the
                resources (defaults to `False`).

        Raises:
            ~fs3.errors.FileExpected: If ``src_path`` maps to a
                directory instead of a file.
            ~fs3.errors.DestinationExists: If ``dst_path`` exists,
                and ``overwrite`` is `False`.
            ~fs3.errors.ResourceNotFound: If a parent directory of
                ``dst_path`` does not exist.

        """
        _src_path = self.validatepath(src_path)
        _dst_path = self.validatepath(dst_path)
        if not overwrite and self.exists(_dst_path):
            raise errors.DestinationExists(dst_path)
        if self.getinfo(_src_path).is_dir:
            raise errors.FileExpected(src_path)
        if _src_path == _dst_path:
            # early exit when moving a file onto itself
            return
        if self.getmeta().get("supports_rename", False):
            try:
                src_sys_path = self.getsyspath(_src_path)
                dst_sys_path = self.getsyspath(_dst_path)
            except errors.NoSysPath:  # pragma: no cover
                pass
            else:
                try:
                    os.rename(src_sys_path, dst_sys_path)
                except OSError:
                    pass
                else:
                    if preserve_time:
                        copy_modified_time(self, _src_path, self, _dst_path)
                    return
        with self._lock:
            with self.open(_src_path, "rb") as read_file:
                # FIXME(@althonos): typing complains because open return IO
                self.upload(_dst_path, read_file)  # type: ignore
            if preserve_time:
                copy_modified_time(self, _src_path, self, _dst_path)
            self.remove(_src_path)

    def open(
        self,
        path,  # type: str
        mode="r",  # type: str
        buffering=-1,  # type: int
        encoding=None,  # type: Optional[str]
        errors=None,  # type: Optional[str]
        newline="",  # type: str
        **options  # type: Any
    ):
        # type: (...) -> IO
        """Open a file.

        Arguments:
            path (str): A path to a file on the filesystem.
            mode (str): Mode to open the file object with
                (defaults to *r*).
            buffering (int): Buffering policy (-1 to use
                default buffering, 0 to disable buffering, 1 to select
                line buffering, of any positive integer to indicate
                a buffer size).
            encoding (str): Encoding for text files (defaults to
                ``utf-8``)
            errors (str, optional): What to do with unicode decode errors
                (see `codecs` module for more information).
            newline (str): Newline parameter.
            **options: keyword arguments for any additional information
                required by the filesystem (if any).

        Returns:
            io.IOBase: a *file-like* object.

        Raises:
            ~fs3.errors.FileExpected: If the path is not a file.
            ~fs3.errors.FileExists: If the file exists, and *exclusive mode*
                is specified (``x`` in the mode).
            ~fs3.errors.ResourceNotFound: If the path does not exist.

        """
        validate_open_mode(mode)
        bin_mode = mode.replace("t", "")
        bin_file = self.openbin(path, mode=bin_mode, buffering=buffering)
        io_stream = iotools.make_stream(
            path,
            bin_file,
            mode=mode,
            buffering=buffering,
            encoding=encoding or "utf-8",
            errors=errors,
            newline=newline,
            **options
        )
        return io_stream

    def opendir(
        self,  # type: _F
        path,  # type: str
        factory=None,  # type: Optional[_OpendirFactory]
    ):
        # type: (...) -> SubFS[FS]
        # FIXME(@althonos): use generics here if possible
        """Get a filesystem object for a sub-directory.

        Arguments:
            path (str): Path to a directory on the filesystem.
            factory (callable, optional): A callable that when invoked
                with an FS instance and ``path`` will return a new FS object
                representing the sub-directory contents. If no ``factory``
                is supplied then `~fs3.subfs_class` will be used.

        Returns:
            ~fs3.subfs.SubFS: A filesystem representing a sub-directory.

        Raises:
            ~fs3.errors.ResourceNotFound: If ``path`` does not exist.
            ~fs3.errors.DirectoryExpected: If ``path`` is not a directory.

        """
        from .subfs import SubFS

        _factory = factory or self.subfs_class or SubFS

        if not self.getinfo(path).is_dir:
            raise errors.DirectoryExpected(path=path)
        return _factory(self, path)

    def removetree(self, dir_path):
        # type: (str) -> None
        """Recursively remove a directory and all its contents.

        This method is similar to `~fs3.base.FS.removedir`, but will
        remove the contents of the directory if it is not empty.

        Arguments:
            dir_path (str): Path to a directory on the filesystem.

        Raises:
            ~fs3.errors.ResourceNotFound: If ``dir_path`` does not exist.
            ~fs3.errors.DirectoryExpected: If ``dir_path`` is not a directory.

        Caution:
            A filesystem should never delete its root folder, so
            ``FS.removetree("/")`` has different semantics: the
            contents of the root folder will be deleted, but the
            root will be untouched::

                >>> home_fs = fs3.open_fs("~")
                >>> home_fs.removetree("/")
                >>> home_fs.exists("/")
                True
                >>> home_fs.isempty("/")
                True

            Combined with `~fs3.base.FS.opendir`, this can be used
            to clear a directory without removing the directory
            itself::

                >>> home_fs = fs3.open_fs("~")
                >>> home_fs.opendir("/Videos").removetree("/")
                >>> home_fs.exists("/Videos")
                True
                >>> home_fs.isempty("/Videos")
                True

        """
        _dir_path = abspath(normpath(dir_path))
        with self._lock:
            walker = walk.Walker(search="depth")
            gen_info = walker.info(self, _dir_path)
            for _path, info in gen_info:
                if info.is_dir:
                    self.removedir(_path)
                else:
                    self.remove(_path)
            if _dir_path != "/":
                self.removedir(dir_path)

    def scandir(
        self,
        path,  # type: str
        namespaces=None,  # type: Optional[Collection[str]]
        page=None,  # type: Optional[tuple[int, int]]
    ):
        # type: (...) -> Iterator[Info]
        """Get an iterator of resource info.

        Arguments:
            path (str): A path to a directory on the filesystem.
            namespaces (list, optional): A list of namespaces to include
                in the resource information, e.g. ``['basic', 'access']``.
            page (tuple, optional): May be a tuple of ``(<start>, <end>)``
                indexes to return an iterator of a subset of the resource
                info, or `None` to iterate over the entire directory.
                Paging a directory scan may be necessary for very large
                directories.

        Returns:
            ~collections.abc.Iterator: an iterator of `Info` objects.

        Raises:
            ~fs3.errors.DirectoryExpected: If ``path`` is not a directory.
            ~fs3.errors.ResourceNotFound: If ``path`` does not exist.

        """
        namespaces = namespaces or ()
        _path = abspath(normpath(path))

        info = (
            self.getinfo(join(_path, name), namespaces=namespaces)
            for name in self.listdir(path)
        )
        iter_info = iter(info)
        if page is not None:
            start, end = page
            iter_info = itertools.islice(iter_info, start, end)
        return iter_info

    def writebytes(self, path, contents):
        # type: (str, bytes) -> None
        # FIXME(@althonos): accept bytearray and memoryview as well ?
        """Copy binary data to a file.

        Arguments:
            path (str): Destination path on the filesystem.
            contents (bytes): Data to be written.

        Raises:
            TypeError: if contents is not bytes.

        """
        if not isinstance(contents, bytes):
            raise TypeError("contents must be bytes")
        with closing(self.open(path, mode="wb")) as write_file:
            write_file.write(contents)

    setbytes = _new_name(writebytes, "setbytes")

    def upload(self, path, file, chunk_size=None, **options):
        # type: (str, BinaryIO, Optional[int], **Any) -> None
        """Set a file to the contents of a binary file object.

        This method copies bytes from an open binary file to a file on
        the filesystem. If the destination exists, it will first be
        truncated.

        Arguments:
            path (str): A path on the filesystem.
            file (io.IOBase): a file object open for reading in
                binary mode.
            chunk_size (int, optional): Number of bytes to read at a
                time, if a simple copy is used, or `None` to use
                sensible default.
            **options: Implementation specific options required to open
                the source file.

        Raises:
            ~fs3.errors.ResourceNotFound: If a parent directory of
                ``path`` does not exist.

        Note that the file object ``file`` will *not* be closed by this
        method. Take care to close it after this method completes
        (ideally with a context manager).

        Example:
            >>> with open('~/movies/starwars.mov', 'rb') as read_file:
            ...     my_fs.upload('starwars.mov', read_file)

        """
        with self._lock:
            with self.openbin(path, mode="wb", **options) as dst_file:
                tools.copy_file_data(file, dst_file, chunk_size=chunk_size)

    setbinfile = _new_name(upload, "setbinfile")

    def writefile(
        self,
        path,  # type: str
        file,  # type: IO
        encoding=None,  # type: Optional[str]
        errors=None,  # type: Optional[str]
        newline="",  # type: str
    ):
        # type: (...) -> None
        """Set a file to the contents of a file object.

        Arguments:
            path (str): A path on the filesystem.
            file (io.IOBase): A file object open for reading.
            encoding (str, optional): Encoding of destination file,
                defaults to `None` for binary.
            errors (str, optional): How encoding errors should be treated
                (same as `io.open`).
            newline (str): Newline parameter (same as `io.open`).

        This method is similar to `~FS.upload`, in that it copies data from a
        file-like object to a resource on the filesystem, but unlike ``upload``,
        this method also supports creating files in text-mode (if the ``encoding``
        argument is supplied).

        Note that the file object ``file`` will *not* be closed by this
        method. Take care to close it after this method completes
        (ideally with a context manager).

        Example:
            >>> with open('myfile.txt') as read_file:
            ...     my_fs.writefile('myfile.txt', read_file)

        """
        mode = "wb" if encoding is None else "wt"

        with self._lock:
            with self.open(
                path, mode=mode, encoding=encoding, errors=errors, newline=newline
            ) as dst_file:
                tools.copy_file_data(file, dst_file)

    setfile = _new_name(writefile, "setfile")

    def settimes(self, path, accessed=None, modified=None):
        # type: (str, Optional[datetime], Optional[datetime]) -> None
        """Set the accessed and modified time on a resource.

        Arguments:
            path: A path to a resource on the filesystem.
            accessed (datetime, optional): The accessed time, or
                `None` (the default) to use the current time.
            modified (datetime, optional): The modified time, or
                `None` (the default) to use the same time as the
                ``accessed`` parameter.

        """
        details = {}  # type: dict
        raw_info = {"details": details}

        details["accessed"] = (
            time.time() if accessed is None else datetime_to_epoch(accessed)
        )

        details["modified"] = (
            details["accessed"] if modified is None else datetime_to_epoch(modified)
        )

        self.setinfo(path, raw_info)

    def writetext(
        self,
        path,  # type: str
        contents,  # type: str
        encoding="utf-8",  # type: str
        errors=None,  # type: Optional[str]
        newline="",  # type: str
    ):
        # type: (...) -> None
        """Create or replace a file with text.

        Arguments:
            path (str): Destination path on the filesystem.
            contents (str): str to be written.
            encoding (str, optional): Encoding of destination file
                (defaults to ``'utf-8'``).
            errors (str, optional): How encoding errors should be treated
                (same as `io.open`).
            newline (str): Newline parameter (same as `io.open`).

        Raises:
            TypeError: if ``contents`` is not a unicode string.

        """
        if not isinstance(contents, str):
            raise TypeError("contents must be unicode")
        with closing(
            self.open(
                path, mode="wt", encoding=encoding, errors=errors, newline=newline
            )
        ) as write_file:
            write_file.write(contents)

    def touch(self, path):
        # type: (str) -> None
        """Touch a file on the filesystem.

        Touching a file means creating a new file if ``path`` doesn't
        exist, or update accessed and modified times if the path does
        exist. This method is similar to the linux command of the same
        name.

        Arguments:
            path (str): A path to a file on the filesystem.

        """
        with self._lock:
            now = time.time()
            if not self.create(path):
                raw_info = {"details": {"accessed": now, "modified": now}}
                self.setinfo(path, raw_info)

    def validatepath(self, path):
        # type: (str) -> str
        """Validate a path, returning a normalized absolute path on sucess.

        Many filesystems have restrictions on the format of paths they
        support. This method will check that ``path`` is valid on the
        underlaying storage mechanism and throw a
        `~fs3.errors.InvalidPath` exception if it is not.

        Arguments:
            path (str): A path.

        Returns:
            str: A normalized, absolute path.

        Raises:
            ~fs3.errors.InvalidPath: If the path is invalid.
            ~fs3.errors.FilesystemClosed: if the filesystem is closed.
            ~fs3.errors.InvalidCharsInPath: If the path contains
                invalid characters.

        """
        self.check()

        if isinstance(path, bytes):
            raise TypeError(f"paths must be str (not bytes). Byte path: '{path}'")

        meta = self.getmeta()

        invalid_chars = typing.cast(str, meta.get("invalid_path_chars"))
        if invalid_chars:
            if set(path).intersection(invalid_chars):
                raise errors.InvalidCharsInPath(path)

        max_sys_path_length = typing.cast(int, meta.get("max_sys_path_length", -1))
        if max_sys_path_length != -1:
            try:
                sys_path = self.getsyspath(path)
            except errors.NoSysPath:  # pragma: no cover
                pass
            else:
                if len(sys_path) > max_sys_path_length:
                    _msg = "path too long (max {max_chars} characters in sys path)"
                    msg = _msg.format(max_chars=max_sys_path_length)
                    raise errors.InvalidPath(path, msg=msg)
        path = abspath(normpath(path))
        return path

    # ---------------------------------------------------------------- #
    # Helper methods                                                   #
    # Filesystems should not implement these methods.                  #
    # ---------------------------------------------------------------- #

    def getbasic(self, path):
        # type: (str) -> Info
        """Get the *basic* resource info.

        This method is shorthand for the following::

            fs3.getinfo(path, namespaces=['basic'])

        Arguments:
            path (str): A path on the filesystem.

        Returns:
            ~fs3.info.Info: Resource information object for ``path``.

        Note:
            .. deprecated:: 2.4.13
                Please use `~FS.getinfo` directly, which is
                required to always return the *basic* namespace.

        """
        warnings.warn(
            "method 'getbasic' has been deprecated, please use 'getinfo'",
            DeprecationWarning,
        )
        return self.getinfo(path, namespaces=["basic"])

    def getdetails(self, path):
        # type: (str) -> Info
        """Get the *details* resource info.

        This method is shorthand for the following::

            fs.getinfo(path, namespaces=['details'])

        Arguments:
            path (str): A path on the filesystem.

        Returns:
            ~fs3.info.Info: Resource information object for ``path``.

        """
        return self.getinfo(path, namespaces=["details"])

    def check(self):
        # type: () -> None
        """Check if a filesystem may be used.

        Raises:
            ~fs3.errors.FilesystemClosed: if the filesystem is closed.

        """
        if self.isclosed():
            raise errors.FilesystemClosed()

    def match(self, patterns, name):
        # type: (Optional[Iterable[str]], str) -> bool
        """Check if a name matches any of a list of wildcards.

        If a filesystem is case *insensitive* (such as Windows) then
        this method will perform a case insensitive match (i.e. ``*.py``
        will match the same names as ``*.PY``). Otherwise the match will
        be case sensitive (``*.py`` and ``*.PY`` will match different
        names).

        Arguments:
            patterns (list, optional): A list of patterns, e.g.
                ``['*.py']``, or `None` to match everything.
            name (str): A file or directory name (not a path)

        Returns:
            bool: `True` if ``name`` matches any of the patterns.

        Raises:
            TypeError: If ``patterns`` is a single string instead of
                a list (or `None`).

        Example:
            >>> my_fs.match(['*.py'], '__init__.py')
            True
            >>> my_fs.match(['*.jpg', '*.png'], 'foo.gif')
            False

        Note:
            If ``patterns`` is `None` (or ``['*']``), then this
            method will always return `True`.

        """
        if patterns is None:
            return True
        if isinstance(patterns, str):
            raise TypeError("patterns must be a list or sequence")
        case_sensitive = not typing.cast(
            bool, self.getmeta().get("case_insensitive", False)
        )
        matcher = wildcard.get_matcher(patterns, case_sensitive)
        return matcher(name)

    def match_glob(self, patterns, path, accept_prefix=False):
        # type: (Optional[Iterable[str]], str, bool) -> bool
        """Check if a path matches any of a list of glob patterns.

        If a filesystem is case *insensitive* (such as Windows) then
        this method will perform a case insensitive match (i.e. ``*.py``
        will match the same names as ``*.PY``). Otherwise the match will
        be case sensitive (``*.py`` and ``*.PY`` will match different
        names).

        Arguments:
            patterns (list, optional): A list of patterns, e.g.
                ``['*.py']``, or `None` to match everything.
            path (str): A resource path, starting with "/".
            accept_prefix (bool): If ``True``, the path is
                not required to match the patterns themselves
                but only need to be a prefix of a string that does.

        Returns:
            bool: `True` if ``path`` matches any of the patterns.

        Raises:
            TypeError: If ``patterns`` is a single string instead of
                a list (or `None`).
            ValueError: If ``path`` is not a string starting with "/".

        Example:
            >>> my_fs.match_glob(['*.py'], '/__init__.py')
            True
            >>> my_fs.match_glob(['*.jpg', '*.png'], '/foo.gif')
            False
            >>> my_fs.match_glob(['dir/file.txt'], '/dir/', accept_prefix=True)
            True
            >>> my_fs.match_glob(['dir/file.txt'], '/dir/', accept_prefix=False)
            False
            >>> my_fs.match_glob(['dir/file.txt'], '/dir/gile.txt', accept_prefix=True)
            False

        Note:
            If ``patterns`` is `None` (or ``['*']``), then this
            method will always return `True`.

        """
        if patterns is None:
            return True
        if not path or path[0] != "/":
            raise ValueError("%s needs to be a string starting with /" % path)
        if isinstance(patterns, str):
            raise TypeError("patterns must be a list or sequence")
        case_sensitive = not typing.cast(
            bool, self.getmeta().get("case_insensitive", False)
        )
        matcher = glob.get_matcher(
            patterns, case_sensitive, accept_prefix=accept_prefix
        )
        return matcher(path)

    def tree(self, **kwargs):
        # type: (**Any) -> None
        """Render a tree view of the filesystem to stdout or a file.

        The parameters are passed to :func:`~fs3.tree.render`.

        Keyword Arguments:
            path (str): The path of the directory to start rendering
                from (defaults to root folder, i.e. ``'/'``).
            file (io.IOBase): An open file-like object to render the
                tree, or `None` for stdout.
            encoding (str): Unicode encoding, or `None` to
                auto-detect.
            max_levels (int): Maximum number of levels to
                display, or `None` for no maximum.
            with_color (bool): Enable terminal color output,
                or `None` to auto-detect terminal.
            dirs_first (bool): Show directories first.
            exclude (list): Option list of directory patterns
                to exclude from the tree render.
            filter (list): Optional list of files patterns to
                match in the tree render.

        """
        from .tree import render

        render(self, **kwargs)

    def hash(self, path, name):
        # type: (str, str) -> str
        """Get the hash of a file's contents.

        Arguments:
            path(str): A path on the filesystem.
            name(str):
                One of the algorithms supported by the `hashlib` module,
                e.g. `"md5"` or `"sha256"`.

        Returns:
            str: The hex digest of the hash.

        Raises:
            ~fs3.errors.UnsupportedHash: If the requested hash is not supported.
            ~fs3.errors.ResourceNotFound: If ``path`` does not exist.
            ~fs3.errors.FileExpected: If ``path`` exists but is not a file.

        """
        self.validatepath(path)
        try:
            hash_object = hashlib.new(name)
        except ValueError:
            raise errors.UnsupportedHash("hash '{}' is not supported".format(name))
        with self.openbin(path) as binary_file:
            while True:
                chunk = binary_file.read(1024 * 1024)
                if not chunk:
                    break
                hash_object.update(chunk)
        return hash_object.hexdigest()