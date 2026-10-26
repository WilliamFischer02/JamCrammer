from __future__ import annotations

from jamcrammer.core.safety import InputLease, KillSwitch, PauseFlag, is_user_active


def test_is_user_active_respects_own_input_and_slack() -> None:
    assert not is_user_active(last_user_input_ts=100.0, last_bot_input_ts=100.0, slack_s=0.5)
    assert not is_user_active(100.4, 100.0, 0.5)
    assert is_user_active(100.6, 100.0, 0.5)
    assert is_user_active(100.0, 0.0, 0.5)


def test_kill_switch() -> None:
    ks = KillSwitch()
    assert not ks.tripped
    ks.trip("f12")
    assert ks.tripped
    assert ks.reason == "f12"
    assert ks.wait(0.01)


def test_pause_toggle() -> None:
    p = PauseFlag()
    assert p.toggle() is True
    assert p.paused
    assert p.toggle() is False
    assert not p.paused


def test_input_lease() -> None:
    lease = InputLease()
    assert lease.acquire("hunter")
    assert lease.acquire("hunter")
    assert not lease.acquire("farmer")
    assert lease.preempt("watchdog") == "hunter"
    assert lease.held_by("watchdog")
    lease.release("hunter")  # not the owner: no-op
    assert lease.owner == "watchdog"
    lease.release("watchdog")
    assert lease.owner is None
