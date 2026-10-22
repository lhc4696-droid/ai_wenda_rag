"""Local-only pytest plugin: replace pytest's numbered tmp dirs.

The DSH file sandbox denies pytest's own basetemp bookkeeping (it re-scans a
directory it just created), which makes every tmp_path-based fixture error out.
This plugin gives tmp_path/tmp_path_factory a plain tempfile.mkdtemp directory
instead. It is a harness workaround, not part of the project.
"""

import pathlib
import shutil
import tempfile

import pytest

_ROOT = pathlib.Path(tempfile.mkdtemp(prefix="dsh-pytest-"))


class _Factory:
    def mktemp(self, basename, numbered=True):
        path = pathlib.Path(tempfile.mkdtemp(prefix=f"{basename}-", dir=_ROOT))
        return path

    def getbasetemp(self):
        return _ROOT


@pytest.fixture(scope="session")
def tmp_path_factory():
    return _Factory()


@pytest.fixture
def tmp_path(tmp_path_factory):
    return tmp_path_factory.mktemp("test")


def pytest_sessionfinish(session, exitstatus):
    shutil.rmtree(_ROOT, ignore_errors=True)
