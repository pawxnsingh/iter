"""Tests for AbortController."""
import pytest

from utils.abort import AbortController, AbortError


def test_abort():
    ctrl = AbortController()
    assert not ctrl.aborted
    ctrl.abort("test")
    assert ctrl.aborted
    assert ctrl.reason == "test"


def test_child_cascading():
    parent = AbortController()
    child = parent.create_child()
    assert not child.aborted
    parent.abort("parent cancelled")
    assert child.aborted
    assert child.reason == "parent cancelled"


def test_check_raises():
    ctrl = AbortController()
    ctrl.check()
    ctrl.abort("test")
    with pytest.raises(AbortError):
        ctrl.check()


def test_double_abort():
    ctrl = AbortController()
    ctrl.abort("first")
    ctrl.abort("second")
    assert ctrl.reason == "first"
