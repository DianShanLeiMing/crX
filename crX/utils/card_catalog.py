"""A complete, deduplicated view of effective numeric formal references."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from crX.bot import card_detection as detection
from crX.bot.card_data import CARD_NAMES_ZH
from crX.utils import formal_library as formal
from crX.utils import reference_hits, reference_preferences

if TYPE_CHECKING:
    from crX.utils.card_review import ReviewSample


@dataclass
class CatalogReference:
    identifier: str
    card_id: str
    builtin: bool = False
    uploaded: bool = False
    sources: list[ReviewSample] = field(default_factory=list)
    preferred: bool = False
    number: int = 0
    hits: int = 0
    cleared: bool = False

    @property
    def name(self):
        return ("首选" if self.preferred else f"参考{self.number}") + ("（内置参考）" if self.builtin else "")

    @property
    def status(self):
        if self.builtin:
            return "内置正式参考"
        if self.uploaded:
            return "已上传"
        return "待上传" if any(sample.status == "included" for sample in self.sources) else "正式参考"


def build_catalog(samples, *, root=None):
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
    cards = {card: list(references.values()) for card, references in cards.items()}
    preferences = reference_preferences.register_references(cards)["cards"]
    hits, cleared = reference_hits.totals(root) if root is not None else ({}, False)
    for card, references in cards.items():
        value = preferences.get(card, {})
        for reference in references:
            reference.preferred = value.get("preferred") == reference.identifier
            reference.number = value.get("numbers", {}).get(reference.identifier, 0)
            reference.hits = hits.get(reference.identifier, 0)
            reference.cleared = cleared
        reference_preferences.sort_references(references, value)
    return cards


def set_preferred_reference(root, card, identifier):
    from crX.utils.card_workflow import _locked, _scan

    with _locked(root):
        data = build_catalog(_scan(root), root=root)
        return reference_preferences.set_preferred(card, identifier, data.get(card, []))


def reorder_references(root, card, ordered, expected):
    from crX.utils.card_workflow import _locked, _scan

    with _locked(root):
        data = build_catalog(_scan(root), root=root)
        return reference_preferences.set_reference_order(card, ordered, expected, data.get(card, []))
