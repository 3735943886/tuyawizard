"""Memory-leak regression tests for TuyaWizard.

Background: rustuya-manager builds a fresh TuyaWizard per operation and
observed a per-cycle RSS leak. The root cause was in the `tuya_sharing`
SDK — it never closes the `requests.Session` connection pools it owns,
nor stops the optional `SharingMQ(threading.Thread)`. `close()` /
context-manager teardown fixes that; see test_lifecycle.py.

These tests prove the *cumulative* property: repeatedly constructing and
closing a TuyaWizard (the long-running-consumer pattern) does NOT
accumulate, across cycles, any of:

  1. live OS threads (the SharingMQ leak),
  2. un-closed requests.Session pools (the RSS leak), or
  3. live TuyaWizard / Manager objects (a reference-cycle leak via the
     token_listener -> wizard back-reference).

We never touch Tuya cloud: `Manager` and `LoginControl` are replaced
with in-process fakes via `unittest.mock.patch`. The fakes deliberately
reproduce all three leak shapes so a regression in `close()` makes the
relevant test fail.

We track *objects/threads/sessions*, not RSS bytes. RSS-based leak
assertions are allocator- and platform-dependent and notoriously flaky
(glibc arena retention, Python's obmalloc never returning arenas, GC
timing); counting live resources is deterministic and pins the actual
contract.
"""

from __future__ import annotations

import gc
import importlib
import threading
import weakref
from unittest.mock import patch

import pytest

# Patch the wizard *module* object directly rather than the string
# "tuyawizard.wizard.Manager": the package re-exports a `wizard` function
# (from .wizard import wizard) that shadows the `wizard` submodule in the
# package namespace, so mock's dotted-string resolution lands on the
# function (no `Manager` attribute) on some Python/mock versions
# (observed failing on 3.9/3.10). Resolving the module via importlib and
# using patch.object is unambiguous everywhere.
_wizard = importlib.import_module("tuyawizard.wizard")


# --------------------------------------------------------------------------
# Fakes that reproduce the SDK's leak shapes without any network.
# --------------------------------------------------------------------------


class _Registry:
    """Per-test bookkeeping for sessions opened/closed by the fakes."""

    def __init__(self) -> None:
        self.sessions_opened = 0
        self.sessions_closed = 0

    @property
    def sessions_leaked(self) -> int:
        return self.sessions_opened - self.sessions_closed


class _FakeSession:
    """Stand-in for requests.Session that records open/close balance."""

    def __init__(self, registry: _Registry) -> None:
        self._registry = registry
        self.closed = False
        self.adapters = {}
        registry.sessions_opened += 1

    def close(self) -> None:
        if not self.closed:
            self.closed = True
            self._registry.sessions_closed += 1


class _FakeMQ(threading.Thread):
    """Mimics SharingMQ's threading.Thread + stop() shape."""

    def __init__(self) -> None:
        super().__init__(daemon=False)
        self._stop_event = threading.Event()
        self._started = threading.Event()

    def run(self) -> None:
        self._started.set()
        self._stop_event.wait(timeout=30)

    def stop(self) -> None:
        self._stop_event.set()


class _FakeCustomerApi:
    def __init__(self, registry: _Registry) -> None:
        self.session = _FakeSession(registry)
        self.token_listener = None


def _make_fakes(registry: _Registry, *, start_mq: bool):
    """Build (FakeManager, FakeLoginControl) bound to `registry`.

    FakeManager intentionally:
      * opens a requests.Session-shaped pool (via _FakeCustomerApi),
      * optionally starts a SharingMQ-shaped background thread, and
      * stashes the token_listener (which holds a back-reference to the
        wizard) on customer_api, recreating the real
        wizard <-> manager reference cycle.

    It is a fresh class per call (no class-level instance registry) so it
    holds no strong references that would mask an object leak.
    """

    class FakeManager:
        def __init__(
            self,
            client_id,
            user_code,
            terminal_id,
            endpoint,
            info,
            token_listener=None,
        ) -> None:
            self.customer_api = _FakeCustomerApi(registry)
            # Mirror the SDK: the token listener is reachable from the
            # manager, forming wizard -> manager -> customer_api ->
            # token_listener -> wizard.
            self.customer_api.token_listener = token_listener
            self.terminal_id = terminal_id
            self.device_map = {}
            self.unload_called = False
            if start_mq:
                self.mq = _FakeMQ()
                self.mq.start()
                assert self.mq._started.wait(timeout=2)
            else:
                self.mq = None

        def update_device_cache(self) -> None:
            # No devices; keeps wizard.fetch_devices() offline.
            return None

        def unload(self) -> None:
            self.unload_called = True

    class FakeLoginControl:
        def __init__(self, *args, **kwargs) -> None:
            self.session = _FakeSession(registry)

    return FakeManager, FakeLoginControl


_INFO = {
    "user_code": "test-user",
    "terminal_id": "fake-terminal",
    "endpoint": "https://example.invalid",
    "access_token": "tok",
    "refresh_token": "rtok",
    "expire_time": 9999999999,
}


def _build_wizard(tmp_path, FakeManager, FakeLoginControl):
    """Construct a TuyaWizard with the SDK patched out and a manager
    initialized, without hitting the network."""
    from tuyawizard import TuyaWizard

    with patch.object(_wizard, "Manager", FakeManager), patch.object(
        _wizard, "LoginControl", FakeLoginControl
    ):
        wizard = TuyaWizard(info_file=str(tmp_path / "creds.json"))
        wizard.info = dict(_INFO)
        wizard.init_manager()
        return wizard


# --------------------------------------------------------------------------
# Class-level leak tests: construct + close() in a loop.
# --------------------------------------------------------------------------

CYCLES = 40


def test_no_thread_leak_across_cycles(tmp_path):
    """Repeated construct -> start MQ -> close() must not grow the live
    thread count. A regression where close() stops stopping the MQ thread
    shows up as active_count() climbing with CYCLES."""
    baseline = threading.active_count()
    reg = _Registry()
    FakeManager, FakeLoginControl = _make_fakes(reg, start_mq=True)

    for _ in range(CYCLES):
        wizard = _build_wizard(tmp_path, FakeManager, FakeLoginControl)
        assert wizard.manager.mq.is_alive()  # a thread really was running
        wizard.close()

    assert threading.active_count() == baseline


def test_no_session_leak_across_cycles(tmp_path):
    """Every requests.Session opened across all cycles (one for
    CustomerApi, one for LoginControl per cycle) must be closed."""
    reg = _Registry()
    FakeManager, FakeLoginControl = _make_fakes(reg, start_mq=False)

    for _ in range(CYCLES):
        wizard = _build_wizard(tmp_path, FakeManager, FakeLoginControl)
        wizard.close()

    # 2 sessions per cycle proves the fakes actually exercised both pools.
    assert reg.sessions_opened == 2 * CYCLES
    assert reg.sessions_leaked == 0


def test_no_object_leak_across_cycles(tmp_path):
    """No TuyaWizard or Manager instance survives its cycle. weakrefs to
    every wizard/manager must all be dead after a gc.collect()."""
    reg = _Registry()
    FakeManager, FakeLoginControl = _make_fakes(reg, start_mq=False)

    refs = []
    for _ in range(CYCLES):
        wizard = _build_wizard(tmp_path, FakeManager, FakeLoginControl)
        refs.append(weakref.ref(wizard))
        refs.append(weakref.ref(wizard.manager))
        wizard.close()
        del wizard

    gc.collect()
    alive = [r for r in refs if r() is not None]
    assert not alive, f"{len(alive)} wizard/manager objects survived their cycle"


def test_close_reclaims_without_cyclic_gc(tmp_path):
    """close() must break the wizard <-> token_listener cycle eagerly, so
    the wizard, its Manager, and the listener are reclaimed by reference
    counting alone — even with the cyclic garbage collector disabled.

    This is the strongest form of the contract: it proves the fix doesn't
    merely defer cleanup to an eventual gc sweep (which a leak-sensitive
    long-running process may never trigger predictably)."""
    reg = _Registry()
    FakeManager, FakeLoginControl = _make_fakes(reg, start_mq=False)

    gc.collect()
    gc.disable()
    try:
        wizard = _build_wizard(tmp_path, FakeManager, FakeLoginControl)
        wref = weakref.ref(wizard)
        mref = weakref.ref(wizard.manager)
        lref = weakref.ref(wizard.manager.customer_api.token_listener)

        wizard.close()
        del wizard

        assert wref() is None, "wizard survived without cyclic GC — cycle not broken"
        assert mref() is None, "Manager survived without cyclic GC"
        assert lref() is None, "token_listener survived without cyclic GC"
    finally:
        gc.enable()


# --------------------------------------------------------------------------
# Module-level leak test: the wizard() convenience function.
#
# Asymmetry guard: TuyaWizard (the class) tears itself down via close()/
# context manager, but the wizard() front-door function must do the same
# on its caller's behalf. Without it, `from tuyawizard import wizard;
# wizard(...)` leaks SDK state on every call.
# --------------------------------------------------------------------------


def test_wizard_function_tears_down_across_calls(tmp_path):
    """Calling the module-level wizard() repeatedly must not accumulate
    threads — it has to close() the TuyaWizard it builds internally."""
    from tuyawizard import wizard as wizard_fn

    baseline = threading.active_count()
    reg = _Registry()
    FakeManager, FakeLoginControl = _make_fakes(reg, start_mq=True)

    creds = dict(_INFO)
    for _ in range(CYCLES):
        with patch.object(_wizard, "Manager", FakeManager), patch.object(
            _wizard, "LoginControl", FakeLoginControl
        ):
            wizard_fn(
                creds=creds,
                device_file=str(tmp_path / "devices.json"),
                creds_file=str(tmp_path / "creds.json"),
            )

    assert threading.active_count() == baseline
    # Each call opens both pools and must close both.
    assert reg.sessions_opened == 2 * CYCLES
    assert reg.sessions_leaked == 0


def test_wizard_function_tears_down_on_exception(tmp_path):
    """If an error escapes mid-call, wizard() must still tear down — the
    context manager runs close() on the exception path.

    We force update_device_cache() to fail, which sends fetch_devices()
    into its qr_login() fallback; the fake's qr_code() then raises an
    error that is *not* swallowed and propagates out of wizard()."""
    from tuyawizard import wizard as wizard_fn

    baseline = threading.active_count()
    reg = _Registry()
    FakeManager, FakeLoginControl = _make_fakes(reg, start_mq=True)

    def fetch_boom(self):
        raise RuntimeError("cloud fetch failed")

    def qr_boom(self, *args, **kwargs):
        raise RuntimeError("escapes wizard")

    creds = dict(_INFO)
    with patch.object(_wizard, "Manager", FakeManager), patch.object(
        _wizard, "LoginControl", FakeLoginControl
    ), patch.object(FakeManager, "update_device_cache", fetch_boom), patch.object(
        FakeLoginControl, "qr_code", qr_boom, create=True
    ):
        with pytest.raises(RuntimeError, match="escapes wizard"):
            wizard_fn(
                creds=creds,
                device_file=str(tmp_path / "devices.json"),
                creds_file=str(tmp_path / "creds.json"),
            )

    assert threading.active_count() == baseline
    assert reg.sessions_leaked == 0
