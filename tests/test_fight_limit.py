"""Completed-match quotas survive recovery and stop before another match begins."""

from threading import Event

import pytest

from crX.__main__ import make_job_dictionary
from crX.bot import states, worker
from crX.interface.enums import UIField
from crX.utils.logger import Logger


@pytest.mark.parametrize("value", [-1, 10001, "", "bad", "1.5", 1.5, True, None])
def test_invalid_fight_limits_are_rejected(value):
    with pytest.raises(ValueError, match="执行局数"):
        make_job_dictionary({UIField.MAX_FIGHTS: value})


@pytest.mark.parametrize("value,expected", [(0, 0), (1, 1), (10000, 10000), ("3", 3)])
def test_valid_limits_and_default_are_normalized(value, expected):
    assert make_job_dictionary({UIField.MAX_FIGHTS: value})[UIField.MAX_FIGHTS] == expected
    assert make_job_dictionary({})[UIField.MAX_FIGHTS] == 0


@pytest.fixture
def gameplay(monkeypatch, tmp_path):
    """Run the real state machine with fake screen/game operations."""
    started_modes = []
    end_results = []
    signal = Event()
    logger = Logger()
    process = worker.WorkerProcess.__new__(worker.WorkerProcess)
    process.shutdown_event = signal
    monkeypatch.setattr(signal, "wait", lambda timeout=None: False)

    def start(emulator, logger, mode):
        if len(started_modes) >= 10:
            signal.set()
            return False
        started_modes.append(mode)
        return True

    monkeypatch.setattr(states, "check_if_battle_mode_is_selected", lambda *args: True)
    monkeypatch.setattr(states, "shop_daily_state", lambda *args: True)
    monkeypatch.setattr(states, "start_fight", start)
    monkeypatch.setattr(states, "do_fight_state", lambda *args, **kwargs: True)
    monkeypatch.setattr(states, "end_fight_state", lambda *args: end_results.pop(0) if end_results else True)
    monkeypatch.setattr(worker, "finish_fight_recording", lambda *args: None)
    monkeypatch.setattr("crX.utils.failure_samples.get_failure_samples_dir", lambda: tmp_path / "failures")
    jobs = make_job_dictionary(
        {
            UIField.MAX_FIGHTS: 2,
            UIField.CLASSIC_2V2_USER_TOGGLE: True,
            UIField.SHOP_DAILY_OFFER_USER_TOGGLE: True,
            UIField.DISABLE_WIN_TRACK_TOGGLE: True,
        }
    )

    class Device:
        def restart(self):
            pass

    return process, Device(), jobs, logger, signal, started_modes, end_results


def test_mixed_modes_share_quota_and_shop_does_not_count(gameplay):
    process, device, jobs, logger, _, started, _ = gameplay
    assert process._run_bot_loop(device, jobs, logger)
    assert started == ["Classic 1v1", "Classic 2v2"]
    assert logger.stats["completed_fights"] == 2
    assert "2/2" in logger.current_status
    # A new start creates a fresh RuntimeContext and session logger.
    logger = Logger()
    assert process._run_bot_loop(device, jobs, logger)
    assert len(started) == 4
    assert logger.stats["completed_fights"] == 2


@pytest.mark.parametrize("stage", ["start_fight", "fight", "end_fight"])
def test_failed_steps_do_not_consume_quota(gameplay, monkeypatch, stage):
    process, device, jobs, logger, _, started, results = gameplay
    if stage == "end_fight":
        results.extend([False, True, True])
    elif stage == "fight":
        outcomes = iter([False, True, True])
        monkeypatch.setattr(states, "do_fight_state", lambda *args, **kwargs: next(outcomes))
    else:
        start = states.start_fight
        outcomes = iter([False, True, True])
        monkeypatch.setattr(states, "start_fight", lambda *args: start(*args) and next(outcomes))
    assert process._run_bot_loop(device, jobs, logger)
    assert len(started) == 3
    assert logger.stats["completed_fights"] == 2
    assert logger.stats["restarts_after_failure"] == 1


def test_zero_limit_keeps_running_until_stopped(gameplay, monkeypatch):
    process, device, jobs, logger, signal, started, _ = gameplay
    jobs[UIField.MAX_FIGHTS] = 0

    def complete(*args):
        if len(started) == 3:
            signal.set()
        return True

    monkeypatch.setattr(states, "end_fight_state", complete)
    assert not process._run_bot_loop(device, jobs, logger)
    assert len(started) == 3
    assert logger.stats["completed_fights"] == 3
    assert "达到执行局数" not in logger.current_status


def test_archive_failure_at_quota_does_not_start_an_extra_match(gameplay, monkeypatch):
    process, device, jobs, logger, _, started, _ = gameplay
    jobs[UIField.MAX_FIGHTS] = 1
    jobs[UIField.RECORD_FIGHTS_TOGGLE] = True
    monkeypatch.setattr(states, "maybe_archive_recordings", lambda **kwargs: (_ for _ in ()).throw(OSError("disk")))
    assert process._run_bot_loop(device, jobs, logger)
    assert len(started) == 1
    assert logger.stats["completed_fights"] == 1
    assert logger.stats["restarts_after_failure"] == 0


def test_worker_cleanup_keeps_the_quota_completion_message(gameplay, monkeypatch):
    process, device, jobs, logger, _, _, _ = gameplay
    process.jobs, process.stats_queue, process.session_log_path = jobs, None, "unused"
    monkeypatch.setattr(worker, "attach_worker_file_logging", lambda _: None)
    monkeypatch.setattr(worker, "ProcessLogger", lambda _: logger)
    monkeypatch.setattr(process, "_setup_emulator", lambda *args: device)
    process.run()
    assert "达到执行局数" in logger.current_status
