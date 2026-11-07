"""Full-screen input shared by annotation and future YOLO inference."""

import math

import numpy as np

from crX.bot.coords import GAME_SCREEN_SIZE, REFERENCE_SCREEN_SIZE

INPUT_CONFIG_NAME = "battlefield_input.json"


def battlefield_region(screen_size=GAME_SCREEN_SIZE) -> tuple[int, int, int, int]:
    if tuple(screen_size) not in (GAME_SCREEN_SIZE, REFERENCE_SCREEN_SIZE):
        raise ValueError("战场输入只支持 720×1280，历史采集批次兼容 360×640")
    return 0, 0, screen_size[0], screen_size[1]


def input_config(screen_size=GAME_SCREEN_SIZE) -> dict:
    left, top, right, bottom = battlefield_region(screen_size)
    return {
        "schema": "crx-battlefield-input/v2",
        "input_mode": "full_screen",
        "screen_size": list(screen_size),
        "crop_ltrb": [left, top, right, bottom],
        "image_size": [right - left, bottom - top],
        "color_order": "BGR",
        "resize": "none",
        "target": "battlefield_units_and_buildings",
    }


def battlefield_image(frame: np.ndarray) -> np.ndarray:
    """Return the complete original screenshot, without cropping or resampling."""
    if not isinstance(frame, np.ndarray) or frame.dtype != np.uint8 or frame.ndim != 3 or frame.shape[2] != 3:
        raise ValueError("战场输入必须为 uint8 BGR 截图")
    if frame.shape[:2] != GAME_SCREEN_SIZE[::-1]:
        raise ValueError("当前战场训练／推理输入必须是完整的 720×1280 原图，不会自动拉伸")
    return frame


def battlefield_box_to_screen(box, screen_size=GAME_SCREEN_SIZE) -> tuple[float, float, float, float]:
    """Validate full-screen xyxy coordinates after the YOLO result's unletterboxing."""
    if len(box) != 4 or any(not math.isfinite(value) for value in box):
        raise ValueError("识别框需要四个有限的像素坐标")
    left, top, right, bottom = battlefield_region(screen_size)
    x1, y1, x2, y2 = box
    if not (0 <= x1 <= x2 <= right - left and 0 <= y1 <= y2 <= bottom - top):
        raise ValueError("识别框超出完整截图范围，请先还原模型缩放和补边")
    return x1 + left, y1 + top, x2 + left, y2 + top
