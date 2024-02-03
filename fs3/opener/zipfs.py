# coding: utf-8
"""`ZipFS` opener definition.
"""
import typing

from .base import Opener
from .errors import NotWriteable
from .registry import registry

if typing.TYPE_CHECKING:
    from ..zipfs import ZipFS  # noqa: F401
    from .parse import ParseResult


@registry.install
class ZipOpener(Opener):
    """`ZipFS` opener."""

    protocols = ["zip"]

    def open_fs(
        self,
        fs_url,  # type: str
        parse_result,  # type: ParseResult
        writeable,  # type: bool
        create,  # type: bool
        cwd,  # type: str
    ):
        # type: (...) -> ZipFS
        from ..zipfs import ZipFS

        if not create and writeable:
            raise NotWriteable("Unable to open existing ZIP file for writing")
        zip_fs = ZipFS(parse_result.resource, write=create)
        return zip_fs