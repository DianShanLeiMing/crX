"""Per-battle diagnostics for enemy Princess Tower fill and visibility."""

from __future__ import annotations

import time

from crX.bot.coords import ENEMY_PRINCESS_BAR_WIDTH
from crX.bot.state_detect import image_has_enemy_princess_ruins, image_is_in_battle, read_enemy_princess_bars


class TowerHealthMonitor:
    def __init__(self, logger):
        self.logger = logger
        self.last_sample = float("-inf")
        self.last_log = float("-inf")
        self.last_message = ""
        self.last_fill: list[int | None] = [None, None]
        self.missing = [0, 0]
        self.missing_since = [0.0, 0.0]
        self.inferred_destroyed = [False, False]
        self.ruins_seen = [0, 0]
        self.last_ruins = [float("-inf"), float("-inf")]
        self.destroyed = [False, False]

    def observe(self, image) -> None:
        now = time.monotonic()
        elapsed = now - self.last_sample
        if elapsed < 1:
            return
        self.last_sample = now
        if elapsed > 8:
            self.missing = [0, 0]
        if not image_is_in_battle(image):
            self.missing = [0, 0]
            self.ruins_seen = [0, 0]
            return

        bars = read_enemy_princess_bars(image)
        descriptions = []
        for index, (fill, track_seen) in enumerate(bars):
            if not self.destroyed[index]:
                # Card deployment and brief occlusion interrupt observation during a battle.
                # Keep visual evidence for a bounded window, but discard it if the bar returns.
                if fill is not None or now - self.last_ruins[index] > 15:
                    self.ruins_seen[index] = 0
                if fill is None and image_has_enemy_princess_ruins(image, index):
                    self.last_ruins[index] = now
                    self.ruins_seen[index] += 1
                    self.destroyed[index] = self.ruins_seen[index] >= 2
            if track_seen:
                self.inferred_destroyed[index] = False
                self.missing[index] = 0
            if self.destroyed[index] or self.inferred_destroyed[index]:
                descriptions.append("已摧毁")
                continue
            if fill is not None:
                self.last_fill[index] = fill
                descriptions.append(f"存在 {fill / ENEMY_PRINCESS_BAR_WIDTH:.0%}（{fill}px）")
                continue
            last = self.last_fill[index]
            if not track_seen and bars[1 - index][0] is not None and last is not None and last <= 16:
                if not self.missing[index]:
                    self.missing_since[index] = now
                self.missing[index] += 1
                # Require several observations over time to rule out brief occlusions.
                if self.missing[index] >= 5 and now - self.missing_since[index] >= 12:
                    self.inferred_destroyed[index] = True
                    descriptions.append("已摧毁")
                    continue
            else:
                self.missing[index] = 0
            descriptions.append("疑似已毁" if self.missing[index] >= 3 else "血条未读出")

        left, right = bars[0][0], bars[1][0]
        if left is None or right is None:
            comparison = "暂无法比较"
        elif abs(left - right) <= 1:
            comparison = "两侧比例接近"
        else:
            comparison = "左侧比例更低" if left < right else "右侧比例更低"
        message = f"敌方公主塔 | 左 {descriptions[0]} | 右 {descriptions[1]}"
        if not (any(self.destroyed) or any(self.inferred_destroyed)):
            message += f" | {comparison}"
        persist = message != self.last_message or now - self.last_log >= 15
        self.logger.log_recognition("图色", message, persist=persist)
        if persist:
            self.last_log = now
            self.last_message = message
