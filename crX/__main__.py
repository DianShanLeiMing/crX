"""Desktop entry point: UI stays responsive while a separate process drives the game."""

from __future__ import annotations

import logging
import multiprocessing as mp
import threading
from queue import Empty, Queue
from typing import Any

from crX.bot.worker import WorkerProcess, stop_worker_process
from crX.emulators import EmulatorType
from crX.emulators.adb import AdbController
from crX.emulators.adb_base import validate_device_serial
from crX.emulators.mumu import MuMuEmulatorController
from crX.interface.config import DEFAULTS, MAX_FIGHT_LIMIT
from crX.interface.enums import UIField, has_start_ready_job
from crX.interface.ui import CrXUI, no_jobs_popup
from crX.utils.caching import USER_SETTINGS_CACHE
from crX.utils.cli_config import arg_parser
from crX.utils.logger import Logger, initialize_pylogging, log_dir, log_name
from crX.utils.open_folder import open_folder
from crX.utils.platform import get_recordings_dir, validate_recordings_path


def make_job_dictionary(values: dict[str, Any]) -> dict[str, Any]:
    jobs: dict[str, Any] = DEFAULTS | {key: value for key, value in values.items() if key in DEFAULTS}
    for key, default in DEFAULTS.items():
        if isinstance(default, bool) and not isinstance(jobs[key], bool):
            raise ValueError(f"勾选项的值无效：{key}")
    for key, maximum in ((UIField.DECK_NUMBER_SELECTION, 5), (UIField.MAX_DECK_SELECTION, 10)):
        try:
            jobs[key] = int(jobs[key])
        except (ValueError, TypeError) as error:
            raise ValueError(f"卡组选择无效：{key}") from error
        if not 1 <= jobs[key] <= maximum:
            raise ValueError(f"卡组编号必须在 1 到 {maximum} 之间")
    raw_limit = jobs[UIField.MAX_FIGHTS]
    message = f"执行局数必须是 0 到 {MAX_FIGHT_LIMIT} 之间的整数，0 表示不限"
    if isinstance(raw_limit, bool) or not isinstance(raw_limit, int | str):
        raise ValueError(message)
    try:
        limit = int(raw_limit)
    except ValueError as error:
        raise ValueError(message) from error
    if not 0 <= limit <= MAX_FIGHT_LIMIT:
        raise ValueError(message)
    jobs[UIField.MAX_FIGHTS] = limit
    jobs[UIField.EMULATOR] = EmulatorType(jobs[UIField.EMULATOR])
    serial = str(jobs[UIField.ADB_SERIAL] or "").strip()
    if not validate_device_serial(serial):
        raise ValueError("ADB 设备序列号无效")
    jobs[UIField.ADB_SERIAL] = serial
    index = str(jobs[UIField.MUMU_INSTANCE])
    if not index.isascii() or not index.isdecimal() or not 0 <= int(index) <= 10000:
        raise ValueError("请选择一个有效的 MuMu 实例编号")
    jobs[UIField.MUMU_INSTANCE] = index
    for key in (UIField.ADB_PATH, UIField.MUMU_PATH, UIField.RECORDING_FOLDER_PATH):
        jobs[key] = str(jobs[key] or "").strip()
    if jobs[UIField.RANDOM_DECKS_USER_TOGGLE] and jobs[UIField.CYCLE_DECKS_USER_TOGGLE]:
        raise ValueError("随机卡组与轮换卡组只能启用一项")
    if jobs[UIField.RECORD_FIGHTS_TOGGLE]:
        valid, message = validate_recordings_path(jobs[UIField.RECORDING_FOLDER_PATH])
        if not valid:
            raise ValueError(message)
    return jobs


class BotApplication:
    def __init__(self, settings=None):
        initialize_pylogging()
        self.ui = CrXUI()
        self.logger = Logger(timed=False)
        self.process = None
        self.stats_queue = None
        self.shutdown_event = None
        self.last_stats = {}
        self._closing = False
        self._device_busy = False
        self.device_results = Queue()
        self.ui.set_all_values(settings or USER_SETTINGS_CACHE.load_data())
        self.current_values = self.ui.get_all_values()
        self.ui.main_btn.configure(command=self._on_main_button)
        self.ui.config_callback = self._on_config_change
        self.ui.device_callback = self._device_action
        self.ui.open_logs_callback = lambda: open_folder(log_dir)
        self.ui.open_recordings_callback = lambda: open_folder(
            get_recordings_dir(custom_path=self.ui.get_all_values()[UIField.RECORDING_FOLDER_PATH])
        )
        self.ui.protocol("WM_DELETE_WINDOW", self._on_close)
        self.ui.set_button_state("idle")
        self._poll()
        self.ui.after(300, lambda: self._device_action("refresh"))

    def _on_main_button(self):
        if self.process and self.process.is_alive():
            self._on_stop()
        else:
            self._on_start()

    def _on_start(self):
        if self._device_busy:
            self.ui.set_status("请等待设备检查完成")
            return
        try:
            jobs = make_job_dictionary(self.ui.get_all_values())
        except (ValueError, TypeError) as error:
            self.ui.set_status(str(error))
            return
        if not has_start_ready_job(jobs):
            no_jobs_popup()
            return
        USER_SETTINGS_CACHE.cache_data(jobs)
        self.stats_queue = mp.Queue(maxsize=100)
        self.shutdown_event = mp.Event()
        self.last_stats = {}
        self.logger = Logger()
        self.logger.change_status("正在启动 crX")
        self.process = WorkerProcess(jobs, self.stats_queue, self.shutdown_event, log_name)
        try:
            self.process.start()
        except Exception as error:
            self.logger.error(f"无法启动任务进程：{error}")
            self.process = None
            self._close_queue()
            return
        self.ui.set_button_state("running")
        self.ui.notebook.select(self.ui.stats_tab)

    def _close_queue(self):
        if self.stats_queue is not None:
            self.stats_queue.cancel_join_thread()
            self.stats_queue.close()
            self.stats_queue = None

    def _on_stop(self):
        self._drain_stats()
        stop_worker_process(self.process, self.shutdown_event)
        self._drain_stats()
        self.process = None
        self._close_queue()
        self.logger.change_status("已停止")
        self.ui.set_button_state("idle")

    def _on_config_change(self, values):
        random_key, cycle_key = UIField.RANDOM_DECKS_USER_TOGGLE, UIField.CYCLE_DECKS_USER_TOGGLE
        if values[random_key] and values[cycle_key]:
            changed = random_key if values[random_key] != self.current_values[random_key] else cycle_key
            other = cycle_key if changed == random_key else random_key
            values[other] = False
            self.ui.set_all_values({other: False})
        self.current_values = values.copy()
        try:
            USER_SETTINGS_CACHE.cache_data(values)
        except OSError as error:
            self.ui.set_status(f"无法保存设置：{error}")

    def _device_action(self, action):
        if self._device_busy or self.process is not None:
            return
        values = self.ui.get_all_values()
        self._device_busy = True
        self.ui.set_status("正在检查设备配置……")

        # Device calls never run on Tk's UI thread. Results are consumed by _poll.
        def run():
            try:
                backend = EmulatorType(values[UIField.EMULATOR])
                if action == "refresh":
                    if backend == EmulatorType.MUMU:
                        instances = MuMuEmulatorController.list_instances(values[UIField.MUMU_PATH])
                        result = ("instances", instances)
                    else:
                        devices = AdbController.discover_devices(values[UIField.ADB_PATH])
                        result = ("devices", devices)
                else:
                    if backend == EmulatorType.MUMU:
                        device = MuMuEmulatorController(
                            self.logger,
                            values[UIField.MUMU_PATH],
                            values[UIField.MUMU_INSTANCE],
                            values[UIField.ADB_PATH],
                        )
                    else:
                        device = AdbController(
                            self.logger, values[UIField.ADB_SERIAL] or None, values[UIField.ADB_PATH]
                        )
                    device.start()
                    if action == "reset":
                        device.reset_screen()
                        result = ("status", "已恢复所选设备的默认分辨率与 DPI")
                    else:
                        reachable, reason = device.is_reachable()
                        if not reachable:
                            raise ValueError(reason)
                        image = device.screenshot()
                        result = (
                            "status",
                            f"已连接 {device.device_serial}；截图尺寸 {image.shape[1]}×{image.shape[0]}",
                        )
                self.device_results.put(result)
            except Exception as error:
                logging.exception("Device check failed")
                self.device_results.put(("status", str(error)))

        threading.Thread(target=run, name="device-check", daemon=True).start()

    def _drain_stats(self):
        if self.stats_queue is None:
            return
        try:
            while True:
                self.last_stats = self.stats_queue.get_nowait()
                self.logger.current_status = self.last_stats.get("current_status", self.logger.current_status)
        except (Empty, OSError, ValueError):
            pass

    def _poll(self):
        if self._closing:
            return
        self._drain_stats()
        if self.process is not None and not self.process.is_alive():
            self.process.join(timeout=0)
            self.process = None
            self._close_queue()
            self.ui.set_button_state("idle")
        stats = dict(self.last_stats)
        if self.process is not None:
            stats["time_since_start"] = self.logger.calc_time_since_start()
        self.ui.update_stats(stats)
        self.ui.set_status(self.logger.current_status)
        try:
            kind, result = self.device_results.get_nowait()
            self._device_busy = False
            if kind == "instances":
                indices = [item["index"] for item in result]
                self.ui.instance_combo.configure(values=indices)
                if indices and self.ui.variables[UIField.MUMU_INSTANCE].get() not in indices:
                    self.ui.variables[UIField.MUMU_INSTANCE].set(indices[0])
                self.ui.instance_names.set(
                    "; ".join(f"{item['index']}: {item.get('name', 'MuMu')}" for item in result)
                    or "未找到已有的模拟器实例"
                )
                self.logger.change_status(f"已找到 {len(result)} 个 MuMu 实例")
            elif kind == "devices":
                if len(result) == 1 and not self.ui.variables[UIField.ADB_SERIAL].get():
                    self.ui.variables[UIField.ADB_SERIAL].set(result[0])
                self.logger.change_status("在线设备：" + (", ".join(result) or "无"))
            else:
                self.logger.change_status(result)
        except Empty:
            pass
        self.ui.after(100, self._poll)

    def _on_close(self):
        self._closing = True
        stop_worker_process(self.process, self.shutdown_event)
        self._close_queue()
        self.ui.destroy()

    def run(self, start_on_run=False):
        if start_on_run:
            self.ui.after(1500, self._on_start)
        self.ui.mainloop()


def main():
    mp.freeze_support()
    try:
        mp.set_start_method("spawn")
    except RuntimeError:
        pass
    args = arg_parser()
    BotApplication().run(start_on_run=args.start)


if __name__ == "__main__":
    main()
