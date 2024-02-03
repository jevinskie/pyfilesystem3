import os
from unittest import mock

from fs3 import errors
from fs3.tempfs import TempFS

from .test_osfs import TestOSFS


class TestTempFS(TestOSFS):
    """Test OSFS implementation."""

    def make_fs(self):
        return TempFS()

    def test_clean(self):
        t = TempFS()
        _temp_dir = t.getsyspath("/")
        self.assertTrue(os.path.isdir(_temp_dir))
        t.close()
        self.assertFalse(os.path.isdir(_temp_dir))

    @mock.patch("shutil.rmtree", create=True)
    def test_clean_error(self, rmtree):
        rmtree.side_effect = Exception("boom")
        with self.assertRaises(errors.OperationFailed):
            t = TempFS(ignore_clean_errors=False)
            t.writebytes("foo", b"bar")
            t.close()
