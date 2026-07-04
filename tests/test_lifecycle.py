"""Regression tests for TuyaWizard.close() / context manager.

Background: downstream consumers (rustuya-manager) observed a per-cycle
RSS leak of ~750-950 KB when creating a fresh TuyaWizard per operation
without any explicit teardown. Root causes traced to the tuya_sharing
SDK: `Manager.unload()` does not stop a `SharingMQ(threading.Thread)`
when one was started, and neither it nor the SDK closes the
`requests.Session` connection pools it owns.

These tests verify that:
  1. `close()` joins (and reaps) a SharingMQ-shaped background thread
     so `threading.active_count()` returns to baseline.
  2. `close()` closes the requests.Session pools owned by
     `CustomerApi` and `LoginControl`.
  3. The context-manager form calls `close()` on exit, including on
     exception.
  4. `close()` is idempotent and tolerant of partial construction.

We mock `Manager` so the tests don't talk to Tuya cloud.
"""

from __future__ import annotations

import importlib
import threading
from unittest.mock import patch, MagicMock

import pytest

# Patch the wizard *module* object directly rather than the string
# "tuyawizard.wizard.Manager": the package re-exports a `wizard` function
# (from .wizard import wizard), which shadows the `wizard` submodule in
# the package namespace, so mock's dotted-string resolution lands on the
# function (no `Manager` attribute) on some Python/mock versions
# (observed failing on 3.9/3.10). Resolving the module via importlib and
# using patch.object is unambiguous everywhere.
_wizard = importlib.import_module("tuyawizard.wizard")


class _FakeMQ(threading.Thread):
    """Mimics SharingMQ's threading.Thread + stop() shape."""

    def __init__(self) -> None:
        super().__init__(daemon=False)
        self._stop_event = threading.Event()
        self.started_signal = threading.Event()
        self.stop_called = False

    def run(self) -> None:
        self.started_signal.set()
        # Block until close() asks us to stop. Mirrors SharingMQ.run's
        # _stop_event.wait() shape.
        self._stop_event.wait(timeout=30)

    def stop(self) -> None:
        self.stop_called = True
        self._stop_event.set()


class _FakeSession:
    """Minimal stand-in for requests.Session."""

    def __init__(self) -> None:
        self.closed = False

    def close(self) -> None:
        self.closed = True


class _FakeCustomerApi:
    def __init__(self) -> None:
        self.session = _FakeSession()
        self.token_listener = None


def _make_fake_manager_class(start_mq: bool = True):
    """Return a Manager replacement class that records construction
    and optionally spawns a controlled background thread."""

    class FakeManager:
        instances: list = []

        def __init__(self, *args, **kwargs) -> None:
            self.customer_api = _FakeCustomerApi()
            self.terminal_id = "fake-terminal"
            self.unload_called = False
            if start_mq:
                self.mq = _FakeMQ()
                self.mq.start()
                assert self.mq.started_signal.wait(timeout=2)
            else:
                self.mq = None
            type(self).instances.append(self)

        def unload(self) -> None:
            self.unload_called = True

    return FakeManager


@pytest.fixture
def baseline_threads():
    """active_count() before exercising the wizard, captured outside
    any patch context."""
    return threading.active_count()


def _build_wizard(tmp_path, FakeManager):
    """Build a TuyaWizard with Manager patched and force init_manager
    to succeed without hitting the network."""
    from tuyawizard import TuyaWizard

    with patch.object(_wizard, "Manager", FakeManager):
        wizard = TuyaWizard(info_file=str(tmp_path / "creds.json"))
        wizard.info = {
            "user_code": "test-user",
            "terminal_id": "fake-terminal",
            "endpoint": "https://example.invalid",
            "access_token": "tok",
            "refresh_token": "rtok",
            "expire_time": 9999999999,
        }
        wizard.init_manager()
    return wizard


def test_close_stops_mq_thread_and_resets_baseline(tmp_path, baseline_threads):
    FakeManager = _make_fake_manager_class(start_mq=True)
    wizard = _build_wizard(tmp_path, FakeManager)

    assert wizard.manager is not None
    mq = wizard.manager.mq
    assert mq.is_alive()
    assert threading.active_count() > baseline_threads

    wizard.close()

    assert mq.stop_called
    assert not mq.is_alive()
    assert threading.active_count() == baseline_threads
    assert wizard.manager is None


def test_close_closes_requests_sessions(tmp_path):
    FakeManager = _make_fake_manager_class(start_mq=False)
    wizard = _build_wizard(tmp_path, FakeManager)

    customer_session = wizard.manager.customer_api.session
    login_session = wizard.login.session  # real requests.Session
    assert not customer_session.closed

    wizard.close()

    assert customer_session.closed
    # requests.Session.close() doesn't expose a flag, but the adapters
    # become closed — verify by inspecting one.
    assert all(
        getattr(adapter, "poolmanager", None) is None
        or len(adapter.poolmanager.pools) == 0
        for adapter in login_session.adapters.values()
    )


def test_close_does_not_revoke_by_default(tmp_path):
    """Default close() must NOT call Manager.unload() — that would POST
    to /v1.0/m/token/terminal/expire and kill the saved cloud-side
    tokens, forcing every subsequent run into the QR fallback. This is
    the 0.1.8 regression observed by rustuya-manager."""
    FakeManager = _make_fake_manager_class(start_mq=False)
    wizard = _build_wizard(tmp_path, FakeManager)
    manager = wizard.manager

    wizard.close()

    assert manager.unload_called is False


def test_close_with_revoke_terminal_calls_unload(tmp_path):
    """Explicit revoke_terminal=True still invalidates the cloud
    terminal — the explicit-logout path."""
    FakeManager = _make_fake_manager_class(start_mq=False)
    wizard = _build_wizard(tmp_path, FakeManager)
    manager = wizard.manager

    wizard.close(revoke_terminal=True)

    assert manager.unload_called is True


def test_close_without_revoke_still_tears_down_sessions_and_mq(
    tmp_path, baseline_threads
):
    """The memory-cleanup half of close() (the 0.1.8 fix) must keep
    working under the new no-revoke default."""
    FakeManager = _make_fake_manager_class(start_mq=True)
    wizard = _build_wizard(tmp_path, FakeManager)
    mq = wizard.manager.mq
    customer_session = wizard.manager.customer_api.session
    assert mq.is_alive()
    assert not customer_session.closed

    wizard.close()  # default revoke_terminal=False

    assert not mq.is_alive()
    assert customer_session.closed
    assert threading.active_count() == baseline_threads


def test_close_with_revoke_tolerates_unload_failure(tmp_path):
    """If Manager.unload() raises (e.g. network down), close() still
    completes the local-side teardown."""
    FakeManager = _make_fake_manager_class(start_mq=False)
    wizard = _build_wizard(tmp_path, FakeManager)

    def boom():
        raise RuntimeError("simulated network failure")

    wizard.manager.unload = boom
    customer_session = wizard.manager.customer_api.session

    wizard.close(revoke_terminal=True)  # must not raise

    assert customer_session.closed
    assert wizard.manager is None


def test_context_manager_does_not_revoke(tmp_path):
    """`with TuyaWizard() as w:` must default to no-revoke so the
    saved creds are reusable on the next run."""
    FakeManager = _make_fake_manager_class(start_mq=False)
    from tuyawizard import TuyaWizard

    with patch.object(_wizard, "Manager", FakeManager):
        with TuyaWizard(info_file=str(tmp_path / "creds.json")) as wizard:
            wizard.info = {
                "user_code": "test-user",
                "terminal_id": "fake-terminal",
                "endpoint": "https://example.invalid",
                "access_token": "tok",
                "refresh_token": "rtok",
                "expire_time": 9999999999,
            }
            wizard.init_manager()
            manager = wizard.manager

    assert manager.unload_called is False


def test_close_breaks_token_listener_cycle(tmp_path):
    FakeManager = _make_fake_manager_class(start_mq=False)
    wizard = _build_wizard(tmp_path, FakeManager)

    # Simulate what _TokenListener installation looks like in the real
    # init_manager: CustomerApi holds the listener (which holds wizard).
    listener = MagicMock()
    listener.parent = wizard
    wizard.manager.customer_api.token_listener = listener

    customer_api = wizard.manager.customer_api
    wizard.close()

    assert customer_api.token_listener is None


def test_close_is_idempotent(tmp_path):
    FakeManager = _make_fake_manager_class(start_mq=True)
    wizard = _build_wizard(tmp_path, FakeManager)

    wizard.close()
    # Second close() must not raise even though manager is now None.
    wizard.close()
    wizard.close()


def test_close_tolerates_no_manager(tmp_path):
    """close() before login_auto() — manager never created."""
    from tuyawizard import TuyaWizard

    wizard = TuyaWizard(info_file=str(tmp_path / "creds.json"))
    assert wizard.manager is None
    # Must not raise.
    wizard.close()


def test_close_tolerates_manager_with_no_mq(tmp_path):
    """The common tuyawizard path doesn't call refresh_mq, so
    Manager.mq is None. close() must still tear down sessions."""
    FakeManager = _make_fake_manager_class(start_mq=False)
    wizard = _build_wizard(tmp_path, FakeManager)
    assert wizard.manager.mq is None

    session = wizard.manager.customer_api.session
    wizard.close()
    assert session.closed
    assert wizard.manager is None


def test_context_manager_closes_on_normal_exit(tmp_path, baseline_threads):
    FakeManager = _make_fake_manager_class(start_mq=True)
    from tuyawizard import TuyaWizard

    with patch.object(_wizard, "Manager", FakeManager):
        with TuyaWizard(info_file=str(tmp_path / "creds.json")) as wizard:
            wizard.info = {
                "user_code": "test-user",
                "terminal_id": "fake-terminal",
                "endpoint": "https://example.invalid",
                "access_token": "tok",
                "refresh_token": "rtok",
                "expire_time": 9999999999,
            }
            wizard.init_manager()
            assert threading.active_count() > baseline_threads

    assert threading.active_count() == baseline_threads
    assert wizard.manager is None


def test_context_manager_closes_on_exception(tmp_path, baseline_threads):
    FakeManager = _make_fake_manager_class(start_mq=True)
    from tuyawizard import TuyaWizard

    with pytest.raises(RuntimeError, match="boom"):
        with patch.object(_wizard, "Manager", FakeManager):
            with TuyaWizard(info_file=str(tmp_path / "creds.json")) as wizard:
                wizard.info = {
                    "user_code": "test-user",
                    "terminal_id": "fake-terminal",
                    "endpoint": "https://example.invalid",
                    "access_token": "tok",
                    "refresh_token": "rtok",
                    "expire_time": 9999999999,
                }
                wizard.init_manager()
                raise RuntimeError("boom")

    assert threading.active_count() == baseline_threads
    assert wizard.manager is None
