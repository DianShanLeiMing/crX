"""Review-only appearance groups; capture deduplication remains conservative."""

from dataclasses import dataclass

import cv2
import numpy as np
from PIL import Image

from crX.bot.card_data import CARD_NAMES_ZH
from crX.bot.coords import CARD_FINGERPRINT_SIZE, HAND_CARD_SIZE
from crX.utils.card_review import (
    ReviewSample,
    image_group_features,
    similar_card_images,
)

MAX_SHIFT_X = 8
MAX_SHIFT_Y = 6
MIN_CORRELATION = 0.78
MAX_ALIGNED_DIFFERENCE = 24
MIN_ARTWORK_DEVIATION = 8
MAX_CROP_BYTES = 1024 * 1024


@dataclass
class _Appearance:
    strict: tuple[int, np.ndarray]
    artwork: np.ndarray


@dataclass
class _Reference:
    key: str
    appearance: _Appearance
    card_id: str


def _appearance(sample: ReviewSample) -> _Appearance | None:
    if type(sample.record.get("slot")) is not int or not 0 <= sample.record["slot"] < 4:
        return None
    if sample.record.get("available") is not True:
        return None
    try:
        path = sample.file("card")
        if path.stat().st_size > MAX_CROP_BYTES:
            return None
        with Image.open(path) as image:
            if image.size not in (HAND_CARD_SIZE, CARD_FINGERPRINT_SIZE):
                return None
            strict = image_group_features(image)
            if strict is None:
                return None
            artwork = np.asarray(image.convert("RGB").resize(CARD_FINGERPRINT_SIZE), dtype=np.uint8)
            return _Appearance(strict, artwork)
    except (OSError, ValueError, KeyError, Image.DecompressionBombError):
        return None


def _aligned_match(first: np.ndarray, second: np.ndarray) -> bool:
    """Search only a bounded translation; require both structure and actual color."""
    template = first[MAX_SHIFT_Y:-MAX_SHIFT_Y, MAX_SHIFT_X:-MAX_SHIFT_X]
    if cv2.cvtColor(template, cv2.COLOR_RGB2GRAY).std() < MIN_ARTWORK_DEVIATION:
        return False  # Normalized correlation is ambiguous for flat/obscured crops.
    scores = cv2.matchTemplate(second, template, cv2.TM_CCOEFF_NORMED)
    _, score, _, (left, top) = cv2.minMaxLoc(scores)
    if score < MIN_CORRELATION:
        return False
    height, width = template.shape[:2]
    matched = second[top : top + height, left : left + width]
    difference = np.abs(template.astype(np.int16) - matched.astype(np.int16)).mean()
    return bool(difference <= MAX_ALIGNED_DIFFERENCE)


def _similar(first: _Appearance, second: _Appearance) -> bool:
    if similar_card_images(first.strict, second.strict):
        return True
    # Symmetric checks keep a small matching patch from swallowing unrelated art.
    return _aligned_match(first.artwork, second.artwork) and _aligned_match(second.artwork, first.artwork)


def group_review_samples(samples: list[ReviewSample]) -> dict[str, list[str]]:
    """Group valid human labels first, then compare unlabelled appearances."""
    groups: dict[str, list[str]] = {}
    representatives: list[_Reference] = []
    for sample in samples:
        appearance = _appearance(sample)
        card_id = sample.card_id if sample.card_id in CARD_NAMES_ZH else ""
        matched = None
        if appearance is not None:
            # Human labels include the ordinary/evolution/hero variant in the ID.
            # Prefer that anchor even when an earlier unlabelled image looks closer.
            if card_id:
                matched = next((reference for reference in representatives if reference.card_id == card_id), None)
            if matched is None:
                for reference in representatives:
                    if reference.card_id and card_id and reference.card_id != card_id:
                        continue
                    if _similar(appearance, reference.appearance):
                        matched = reference
                        break
        if matched is None:
            groups[sample.key] = [sample.key]
            if appearance is not None:
                representatives.append(_Reference(sample.key, appearance, card_id))
        else:
            groups[matched.key].append(sample.key)
            if card_id:
                matched.card_id = card_id
    return groups
