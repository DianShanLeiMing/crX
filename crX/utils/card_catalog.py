"""A complete, deduplicated view of effective formal references and local crops."""

from __future__ import annotations

from dataclasses import dataclass, field

from PIL import Image

from crX.bot import card_detection as detection
from crX.bot.card_data import CARD_NAMES_ZH
from crX.utils import formal_library as formal
from crX.utils.card_review import ReviewSample, load_card_thumbnail


@dataclass
class CatalogReference:
    identifier: str
    card_id: str
    builtin: bool = False
    uploaded: bool = False
    sources: list[ReviewSample] = field(default_factory=list)

    @property
    def status(self):
        if self.builtin:
            return "内置正式参考"
        if self.uploaded:
            return "已上传"
        return "待上传" if any(sample.status == "included" for sample in self.sources) else "正式参考"


def build_catalog(samples):
    cards = {card: {} for card in CARD_NAMES_ZH}
    for name, value in zip(detection._REFERENCE_NAMES, detection._REFERENCE_MATRIX, strict=True):
        card = detection.CARD_DETECTION_ALIASES.get(name, name)
        identifier = formal._signature(card, value.tolist())
        cards.setdefault(card, {})[identifier] = CatalogReference(identifier, card, builtin=True, uploaded=True)
    for entry in formal._read_entries(formal.formal_path()):
        cards.setdefault(entry["card_id"], {}).setdefault(entry["id"], CatalogReference(entry["id"], entry["card_id"]))
    for sample in samples:
        reference = cards.get(sample.card_id, {}).get(sample.review.get("formal_id"))
        if reference is not None and sample.status in ("included", "covered"):
            reference.sources.append(sample)
            reference.uploaded |= sample.uploaded
    # Catalogs on machines without source samples still show shared references as uploaded.
    from crX.utils.card_upload import published_reference_ids

    published = published_reference_ids()
    for references in cards.values():
        for reference in references.values():
            reference.uploaded |= reference.identifier in published or not formal.source_checkout()
    return {card: list(references.values()) for card, references in cards.items()}


def reference_crop(reference, size=(24, 30)):
    """Missing source images stay missing; fingerprints cannot reconstruct artwork."""
    for sample in reference.sources:
        if (preview := load_card_thumbnail(sample, size=size)) is not None:
            return preview
    return None


def preview_crop(reference):
    image = reference_crop(reference, size=(160, 200))
    if image is not None:
        scale = min(160 / image.width, 200 / image.height)
        image = image.resize(
            (max(1, round(image.width * scale)), max(1, round(image.height * scale))), Image.Resampling.LANCZOS
        )
    return image
