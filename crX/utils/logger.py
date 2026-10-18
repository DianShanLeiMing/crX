"""import logging for file logging"""

import logging
import time
import zipfile
from os import listdir, makedirs, remove
from os.path import abspath, basename, exists, getmtime, join, normcase

from crX.utils.platform import get_log_dir
from crX.utils.versioning import __version__

LOGS_TO_KEEP = 10

log_dir = get_log_dir("crX")
log_name = join(log_dir, time.strftime("%Y-%m-%d_%H-%M", time.localtime()) + ".txt")
archive_name: str = join(log_dir, "logs.zip")


def compress_logs() -> None:
    """Archive will contain a large text file, all old logs appended together
    extract the file and read the text to get the old logs
    """
    logs: list[str] = sorted(
        [join(log_dir, log) for log in listdir(log_dir) if log.endswith(".txt")],
        key=getmtime,
    )
    if len(logs) >= LOGS_TO_KEEP:
        with zipfile.ZipFile(
            archive_name,
            "a" if exists(archive_name) else "w",
        ) as archive:
            for log in logs[:-LOGS_TO_KEEP]:
                # Use basename so this works on all platforms (Windows, macOS, Linux)
                archive.write(log, basename(log))
                remove(log)


def _normalized_log_path(path: str) -> str:
    return normcase(abspath(path))


def _attach_log_file_handler(log_path: str) -> None:
    """Attach a log-file handler if this process does not already have one for log_path."""
    if not exists(log_dir):
        makedirs(log_dir)

    root_logger = logging.getLogger()
    root_logger.setLevel(logging.DEBUG)

    target_log = _normalized_log_path(log_path)
    for handler in root_logger.handlers:
        if isinstance(handler, logging.FileHandler):
            try:
                if _normalized_log_path(handler.baseFilename) == target_log:
                    return
            except Exception:
                continue

    file_handler = logging.FileHandler(log_path, encoding="utf-8")
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(logging.Formatter("%(levelname)s:%(asctime)s %(message)s"))
    root_logger.addHandler(file_handler)


def attach_worker_file_logging(session_log_path: str) -> None:
    """Attach file logging in the bot worker subprocess.

    macOS/Windows use spawn for multiprocessing, so the worker does not inherit
    the GUI process's logging handlers.
    """
    _attach_log_file_handler(session_log_path)


def initialize_pylogging() -> None:
    """Method to be called once to initialize python logging"""
    _attach_log_file_handler(log_name)

    logging.info("Logging initialized for %s", __version__)
    logging.info("Log directory: %s", log_dir)
    logging.info("Current log file: %s", log_name)
    logging.info("crX %s", __version__)
    compress_logs()


class Logger:
    """Session statistics shared with the UI via ProcessLogger."""

    def __init__(self, stats=None, timed: bool = True) -> None:
        self.start_time = time.time() if timed else None
        self.current_status = "待机"
        self.current_state = "start"
        self._console_enabled = False
        self.errored = False
        self.failure_frame: dict | None = None
        self.stats = dict.fromkeys(
            (
                "wins",
                "completed_fights",
                "losses",
                "cards_played",
                "classic_1v1_fights",
                "classic_2v2_fights",
                "trophy_road_1v1_fights",
                "card_randomizations",
                "card_cycles",
                "shop_offer_collections",
                "restarts_after_failure",
                "current_win_streak",
                "best_win_streak",
            ),
            0,
        )
        self.stats.update(stats or {})

    def get_stats(self):
        wins, losses = self.stats["wins"], self.stats["losses"]
        return self.stats | {
            "current_status": self.current_status,
            "winrate": f"{100 * wins / (wins + losses):.1f}%" if wins + losses else "0%",
            "time_since_start": self.calc_time_since_start(),
        }

    def calc_time_since_start(self) -> str:
        seconds = max(0, int(time.time() - self.start_time)) if self.start_time else 0
        hours, remainder = divmod(seconds, 3600)
        minutes, seconds = divmod(remainder, 60)
        return f"{hours:02}:{minutes:02}:{seconds:02}"

    def _update_log(self) -> None:
        logging.info(self.current_status)

    def log(self, message) -> None:
        logging.info("%s", message)
        if self._console_enabled:
            print(message)

    def error(self, message) -> None:
        self.errored = True
        self.current_status = str(message)
        logging.error("%s", message)
        self._update_log()

    def change_status(self, status) -> None:
        self.current_status = str(status)
        self._update_log()

    def set_current_state(self, state, console=True) -> None:
        self.current_state = state
        self._console_enabled = console

    def _increment(self, key):
        self.stats[key] += 1
        self._update_log()

    def add_win(self):
        self.stats["wins"] += 1
        self.stats["current_win_streak"] += 1
        self.stats["best_win_streak"] = max(self.stats["best_win_streak"], self.stats["current_win_streak"])
        self._update_log()

    def add_loss(self):
        self.stats["losses"] += 1
        self.stats["current_win_streak"] = 0
        self._update_log()

    def add_card_played(self):
        self._increment("cards_played")

    def add_completed_fight(self):
        self._increment("completed_fights")

    def get_cards_played(self):
        return self.stats["cards_played"]

    def add_card_randomization(self):
        self._increment("card_randomizations")

    def add_deck_cycled(self):
        self._increment("card_cycles")

    def increment_classic_1v1_fights(self):
        self._increment("classic_1v1_fights")

    def increment_classic_2v2_fights(self):
        self._increment("classic_2v2_fights")

    def increment_trophy_road_fights(self):
        self._increment("trophy_road_1v1_fights")

    def add_shop_offer_collection(self):
        self._increment("shop_offer_collections")

    def add_restart_after_failure(self):
        self._increment("restarts_after_failure")

    def log_job_dictionary(self, jobs):
        self.log({key: value for key, value in jobs.items() if not key.endswith("path")})


class ProcessLogger(Logger):
    def __init__(self, stats_queue, timed=True):
        super().__init__(timed=timed)
        self._stats_queue = stats_queue

    def _update_log(self):
        super()._update_log()
        from queue import Full

        try:
            self._stats_queue.put_nowait(self.get_stats())
        except (Full, ValueError, OSError):
            pass
