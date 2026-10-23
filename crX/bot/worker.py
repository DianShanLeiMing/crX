"""Worker process and bounded recovery around the serial state machine."""

import logging
import subprocess
import traceback
from contextlib import contextmanager
from multiprocessing import Process, Queue
from multiprocessing.synchronize import Event
from typing import Any, Protocol

from crX.bot.card_samples import finish_card_samples
from crX.bot.recorder import finish_fight_recording
from crX.bot.states import RuntimeContext, state_tree
from crX.emulators import EmulatorType, get_emulator_registry
from crX.emulators.base import EmulatorNotReadyError
from crX.interface.enums import UIField
from crX.utils.failure_samples import FailureSampleRecorder
from crX.utils.logger import ProcessLogger, attach_worker_file_logging


@contextmanager
def screenshot_retry_policy(emulator, logger, shutdown_event):
    """Retry one timed-out read, without replaying a state or any device input.

    The worker owns recovery policy. Adapters retain a single bounded I/O attempt;
    patching the instance also covers screenshot calls inside adapter.restart().
    """
    original = getattr(emulator, "screenshot", None)
    if original is None:
        yield
        return
    overridden = "screenshot" in vars(emulator)

    def capture():
        try:
            return original()
        except subprocess.TimeoutExpired as error:
            command = error.cmd
            if not isinstance(command, list | tuple) or list(command[-3:]) != ["exec-out", "screencap", "-p"]:
                raise
            logger.change_status("ADB 截图超时，正在重试一次；暂不重启游戏")
            if shutdown_event.wait(0.5):
                raise
            return original()

    emulator.screenshot = capture
    try:
        yield
    finally:
        if overridden:
            emulator.screenshot = original
        else:
            del emulator.screenshot


class WorkerProcess(Process):
    def __init__(self, jobs: dict[str, Any], stats_queue: Queue, shutdown_event: Event, session_log_path: str):
        super().__init__(daemon=True)
        self.jobs = jobs
        self.stats_queue = stats_queue
        self.shutdown_event = shutdown_event
        self.session_log_path = session_log_path

    def _setup_emulator(self, jobs, logger):
        selection = jobs.get("emulator", EmulatorType.MUMU)
        cls = get_emulator_registry().get(selection)
        if cls is None or not cls.is_supported_on_current_platform():
            logger.error("当前系统不支持所选设备连接方式")
            return None
        try:
            kwargs = {"adb_path": jobs.get(UIField.ADB_PATH, "")}
            if selection == EmulatorType.MUMU:
                kwargs.update(
                    mumu_path=jobs.get(UIField.MUMU_PATH, ""), instance_index=jobs.get(UIField.MUMU_INSTANCE, "0")
                )
            else:
                kwargs["device_serial"] = jobs.get(UIField.ADB_SERIAL) or None
            emulator = cls(logger, **kwargs)
            with screenshot_retry_policy(emulator, logger, self.shutdown_event):
                emulator.restart()
            return emulator
        except (EmulatorNotReadyError, OSError, ValueError) as error:
            logger.error(f"设备尚未就绪：{error}")
            return None

    def _run_bot_loop(self, emulator, jobs, logger) -> bool:
        diagnostics = None
        try:
            diagnostics = FailureSampleRecorder()
        except Exception:
            logging.warning("异常采样未能启动，继续执行任务", exc_info=True)
        try:
            with screenshot_retry_policy(emulator, logger, self.shutdown_event):
                return self._run_bot_states(emulator, jobs, logger, diagnostics)
        finally:
            if diagnostics is not None:
                diagnostics.close()

    def _run_bot_states(self, emulator, jobs, logger, diagnostics) -> bool:
        state, recoveries = "start", 0
        context = RuntimeContext()
        max_fights = jobs.get(UIField.MAX_FIGHTS, 0)
        while not self.shutdown_event.is_set():
            logger.failure_frame = None
            exception = None
            completed = context.completed_jobs
            try:
                new_state = state_tree(emulator, logger, state, jobs, context)
            except Exception:
                logging.exception("State failed: %s", state)
                exception = traceback.format_exc()[-8000:]
                new_state = "restart"
            if new_state == "rewards_finished":
                logger.change_status(f"检测到对战奖励已领完，任务已停止（已完成 {context.completed_fights} 局）")
                return True
            # Check before recovery: a recording archive failure must not start an extra match.
            if max_fights and context.completed_fights >= max_fights:
                logger.change_status(f"已完成 {context.completed_fights}/{max_fights} 局，达到执行局数，任务已停止")
                return True
            # Reset only after useful work; merely skipping a state is not recovery.
            if context.completed_jobs > completed:
                recoveries = 0
            if new_state == "restart":
                if diagnostics is not None:
                    try:
                        diagnostics.offer(
                            emulator,
                            logger,
                            state=state,
                            attempt=recoveries + 1,
                            session_log_path=getattr(self, "session_log_path", None),
                            context={"mode": context.selected_mode, "completed_fights": context.completed_fights},
                            exception=exception,
                        )
                    except Exception:
                        logging.warning("异常采样失败，继续恢复", exc_info=True)
                finish_card_samples()
                finish_fight_recording(None)
                recoveries += 1
                if recoveries >= 5:
                    logger.error("连续 5 次恢复失败，任务已停止，请检查设备和游戏")
                    return False
                logger.add_restart_after_failure()
                logger.change_status(f"正在恢复游戏（{recoveries}/5）")
                time_to_wait = min(2**recoveries, 10)
                self.shutdown_event.wait(time_to_wait)
            state = new_state
        return False

    def run(self):
        attach_worker_file_logging(self.session_log_path)
        logger = ProcessLogger(self.stats_queue)
        stop_condition_reached = False
        try:
            emulator = self._setup_emulator(self.jobs, logger)
            if emulator is not None:
                stop_condition_reached = self._run_bot_loop(emulator, self.jobs, logger)
        except Exception as error:
            logging.exception("Worker failed")
            logger.error(f"任务已停止：{error}")
        finally:
            finish_card_samples()
            finish_fight_recording(None)
            if not logger.errored and not stop_condition_reached:
                logger.change_status("任务已停止")


class _StoppableProcess(Protocol):
    """Structural interface for the process-lifecycle methods ``stop_worker_process`` needs.

    Satisfied by ``multiprocessing.Process`` (and ``WorkerProcess``), spawned context
    processes, and test fakes alike — so the helper depends on the contract, not the
    concrete class.
    """

    def is_alive(self) -> bool: ...
    def terminate(self) -> None: ...
    def join(self, timeout: float | None = ...) -> None: ...
    def kill(self) -> None: ...


class _Signal(Protocol):
    """Structural interface for the shutdown event — only ``set()`` is used here."""

    def set(self) -> None: ...


def stop_worker_process(
    process: _StoppableProcess | None,
    shutdown_event: _Signal | None = None,
    *,
    graceful_timeout: float = 2.0,
) -> None:
    """Stop the worker process via OS-level termination.

    Signals shutdown (belt-and-suspenders), then terminates and hard-kills if the
    process does not exit within ``graceful_timeout``. The OS interrupts any blocking
    call in the worker (sleeps, ADB/socket waits), so this does not depend on the
    worker cooperatively noticing the shutdown event.
    """
    if shutdown_event is not None:
        shutdown_event.set()
    if process is None or not process.is_alive():
        return
    process.terminate()  # SIGTERM / TerminateProcess
    process.join(timeout=graceful_timeout)
    if process.is_alive():
        process.kill()  # SIGKILL
        process.join(timeout=graceful_timeout)
