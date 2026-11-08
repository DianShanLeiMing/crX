"""Validate YOLO detection annotations and split entire battles into train/val."""

from __future__ import annotations

import hashlib
import io
import json
import math
import random
import re
import shutil
import tempfile
from collections import Counter
from dataclasses import dataclass, replace
from pathlib import Path
from typing import TYPE_CHECKING

from PIL import Image

from crX.detection.battlefield import INPUT_CONFIG_NAME, input_config
from crX.utils.card_review import atomic_json

if TYPE_CHECKING:
    from threading import Event

IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg"}
FLAT_IMAGE_NAME = re.compile(
    r"(?P<group>[A-Za-z0-9_-]{1,160})__(?P<frame>[0-9]{6})" r"(?:_export(?P<export>[0-9a-f]{32}))?(?:_dup[0-9]{3,})?$"
)
MAX_IMAGES = 50000
MAX_IMAGE_BYTES = 16 * 1024 * 1024
MAX_LABEL_BYTES = 512 * 1024
CLASS_LIST_GUIDE = """classes.txt 建议格式（每行一个类别名）：
enemy_knight
friendly_knight
enemy_archers
friendly_archers

上例编号对应：0=敌方骑士，1=己方骑士，2=敌方弓箭手，3=己方弓箭手。
文件里只填写类别名，无需添加编号、逗号或注释，中间不能有空行。
优先使用标注工具导出的类别表；示例需按实际标注的类别和顺序修改。
保存为 UTF-8 文本后，再点击“载入并检查”。
"""
IMPORT_GUIDE = """YOLO 标注数据导入

先按对局分批采集，在截图采集页打开采集总目录并「筛选图片」。
通过「查看筛选结果」筛选后，点击「导出图片」，只输出平铺的 images 图片。
完全重复只留一张；相似画面按内容自动保留代表帧，不按时间补留，查看筛选结果仅用于浏览。原始采集批次不会删除。
文件名含采集时间、场次标识、图片编号和导出唯一标识，必须保留文件名，才能按整场划分。
同一批图片导出到不同新目录时也不会同名；导出唯一标识不参与场次划分。

标注完成后的示例：
  classes.txt
  images/批次名-id标识__000000_export唯一标识.png
  images/批次名-id标识__000001_export唯一标识.png
  labels/批次名-id标识__000000_export唯一标识.txt
  labels/批次名-id标识__000001_export唯一标识.txt

标签也可由标注工具直接保存在 images 中，与图片同名。
classes.txt 使用标注工具导出的类别表，每行一个类别名，编号从 0 开始。
标签为 YOLO 检测格式：class x_center y_center width height，坐标归一化至 0–1。
必须为每张图片提供同名标签；确认没有目标的图片使用空 txt 文件。
不裁剪、不缩放完整原图；全部场次的类别编号保持一致。
仅标注战场兵种和建筑，手牌、圣水仍使用现有规则识别。

进入「标注与划分」，选择包含 images 的根目录和类别表。
平铺图片会按文件名中的场次标识划分，不能随意重命名或去掉前缀。
旧 images/场次/图片、labels/场次/标签 结构仍可单独载入，不能与平铺结构混用。
程序不会把仅有数字、无法确认场次的平铺图片随机拆分到两边。
"""


class DatasetCancelledError(Exception):
    """A preparation operation was stopped before committing an output dataset."""


class ClassListFormatError(ValueError):
    """The class list needs correction; the UI presents a concrete format example."""


@dataclass(frozen=True)
class AnnotatedImage:
    image: Path
    label: Path
    relative: str
    group: str
    digest: str
    label_text: str
    classes: tuple[int, ...]


@dataclass(frozen=True)
class AnnotationScan:
    root: Path
    classes_path: Path
    names: tuple[str, ...]
    images: tuple[AnnotatedImage, ...]
    duplicates: int
    source_groups: int
    input_profile: dict | None = None
    profile_from_images: bool = False


@dataclass(frozen=True)
class DatasetPlan:
    scan: AnnotationScan
    validation_percent: int
    seed: int
    train: tuple[AnnotatedImage, ...]
    val: tuple[AnnotatedImage, ...]
    warnings: tuple[str, ...]


def ensure_annotation_inbox(directory: Path) -> None:
    """Create an empty import layout without replacing user exports."""
    (directory / "images").mkdir(parents=True, exist_ok=True)
    (directory / "labels").mkdir(exist_ok=True)
    for name, content in (("classes.txt", ""), ("导入说明.txt", IMPORT_GUIDE)):
        try:
            with (directory / name).open("x", encoding="utf-8") as stream:
                stream.write(content)
        except FileExistsError:
            pass


def _check_stop(stop: Event | None) -> None:
    if stop is not None and stop.is_set():
        raise DatasetCancelledError("已取消")


def _read_limited(path: Path, limit: int) -> bytes:
    with path.open("rb") as stream:
        value = stream.read(limit + 1)
    if len(value) > limit:
        raise ValueError(f"文件超过允许大小：{path.name}")
    return value


def _inside(root: Path, path: Path) -> Path:
    resolved = path.resolve(strict=True)
    if not resolved.is_relative_to(root) or path.is_symlink() or path.is_junction():
        raise ValueError(f"导入路径不能指向目录外部或链接：{path.name}")
    return resolved


def read_classes(path: Path) -> tuple[str, ...]:
    try:
        lines = _read_limited(path, MAX_LABEL_BYTES).decode("utf-8-sig").splitlines()
    except UnicodeDecodeError as error:
        raise ClassListFormatError("类别表请保存为 UTF-8 文本文件") from error
    names = tuple(line.strip() for line in lines)
    if not names or len(names) > 2048 or any(not name or len(name) > 200 for name in names):
        raise ClassListFormatError("类别表不能为空；classes.txt 每行一个类别名，中间不能有空行")
    if len(set(names)) != len(names) or any(any(ord(char) < 32 for char in name) for name in names):
        raise ClassListFormatError("类别名不能重复或包含控制字符")
    return names


def read_labels(path: Path, names: tuple[str, ...]) -> tuple[str, tuple[int, ...]]:
    rows = []
    classes = []
    text = _read_limited(path, MAX_LABEL_BYTES).decode("utf-8-sig")
    for line_number, line in enumerate(text.splitlines(), 1):
        if not line.strip():
            continue
        parts = line.split()
        prefix = f"{path.name} 第 {line_number} 行"
        if len(parts) != 5 or not re.fullmatch(r"[0-9]+", parts[0]):
            raise ValueError(f"{prefix}：需要 class x_center y_center width height 五列")
        class_id = int(parts[0])
        if class_id >= len(names):
            raise ValueError(f"{prefix}：类别编号 {class_id} 不在类别表中")
        try:
            x, y, width, height = (float(value) for value in parts[1:])
        except ValueError as error:
            raise ValueError(f"{prefix}：坐标不是有效数字") from error
        if not all(math.isfinite(value) for value in (x, y, width, height)):
            raise ValueError(f"{prefix}：坐标必须是有限数字")
        if not (0 <= x <= 1 and 0 <= y <= 1 and 0 < width <= 1 and 0 < height <= 1):
            raise ValueError(f"{prefix}：坐标必须归一化到 0–1，宽高必须大于 0")
        if x - width / 2 < -1e-6 or y - height / 2 < -1e-6 or x + width / 2 > 1 + 1e-6 or y + height / 2 > 1 + 1e-6:
            raise ValueError(f"{prefix}：标注框超出图片范围")
        rows.append(" ".join((str(class_id), *(format(value, ".10g") for value in (x, y, width, height)))))
        classes.append(class_id)
    # Stable ordering also makes duplicate-image annotation comparisons independent of box order.
    return "".join(line + "\n" for line in sorted(rows)), tuple(classes)


def _image_digest(content: bytes, expected_size=None) -> str:
    with Image.open(io.BytesIO(content)) as image:
        if image.format not in {"PNG", "JPEG"} or image.width * image.height > 4096 * 4096:
            raise ValueError("图片必须为 PNG/JPEG，且像素数不超过 4096×4096")
        if expected_size is not None and image.size != tuple(expected_size):
            raise ValueError("标注图片尺寸与完整截图配置不一致，请使用「导出图片」导出的完整截图，不要改尺寸或裁剪")
        rgb = image.convert("RGB")
        digest = hashlib.sha256(str(rgb.size).encode("ascii"))
        digest.update(rgb.tobytes())
        return digest.hexdigest()


def _read_input_profile(root: Path) -> dict | None:
    path = root / INPUT_CONFIG_NAME
    if not path.exists():
        return None
    profile = json.loads(_read_limited(_inside(root, path), 8192).decode("utf-8"))
    if profile != input_config():
        raise ValueError("输入配置不是当前完整截图方案，请使用完整截图重新准备和标注，不能混入旧裁剪标签")
    return profile


def scan_annotations(root: Path, classes_path: Path, *, stop: Event | None = None, progress=None) -> AnnotationScan:
    _check_stop(stop)
    root = root.resolve(strict=True)
    profile = _read_input_profile(root)
    names = read_classes(classes_path)
    image_root = _inside(root, root / "images")
    label_root = _inside(root, root / "labels") if (root / "labels").is_dir() else image_root
    profile_from_images = False
    flat_layout = None
    images = []
    seen_labels = set()
    unique = {}
    parents = {}
    duplicates = 0

    def group_root(group):
        parents.setdefault(group, group)
        while parents[group] != group:
            parents[group] = parents[parents[group]]
            group = parents[group]
        return group

    # Read only files under the chosen image tree. No paths from YAML/manifests are executed or followed.
    for path in sorted(image_root.rglob("*")):
        _check_stop(stop)
        if path.is_symlink() or path.is_junction():
            raise ValueError(f"图片目录包含链接：{path.name}")
        if not path.is_file() or path.suffix.lower() not in IMAGE_SUFFIXES:
            continue
        image_path = _inside(root, path)
        relative = path.relative_to(image_root)
        is_flat = len(relative.parts) == 1
        if flat_layout is not None and flat_layout != is_flat:
            raise ValueError("请勿混用平铺图片和旧场次子目录，请分别载入")
        flat_layout = is_flat
        if is_flat:
            match = FLAT_IMAGE_NAME.fullmatch(path.stem)
            if match is None:
                raise ValueError("图片缺少场次标识：请保留整理工具生成的「批次名__图片编号」文件名")
            group = match["group"]
            if profile is None:
                profile = input_config()
                profile_from_images = True
        else:
            group = relative.parts[0]
        label_relative = relative.with_suffix(".txt")
        if label_relative in seen_labels:
            raise ValueError(f"图片名称存在歧义（不同扩展名对应同一标签）：{relative}")
        seen_labels.add(label_relative)
        label_path = label_root / label_relative
        inline_label = image_root / label_relative
        if is_flat and not label_path.is_file():
            label_path = inline_label
        if not label_path.is_file():
            raise ValueError(f"缺少标签：labels/{label_relative.as_posix()}；无目标图片也需要空 txt")
        label_path = _inside(root, label_path)
        label_text, class_ids = read_labels(label_path, names)
        if is_flat and inline_label.is_file() and inline_label.resolve() != label_path:
            if read_labels(_inside(root, inline_label), names)[0] != label_text:
                raise ValueError(f"同一图片存在两份不同标签：{relative}")
        digest = _image_digest(_read_limited(image_path, MAX_IMAGE_BYTES), profile["image_size"] if profile else None)
        group_root(group)
        item = AnnotatedImage(image_path, label_path, relative.as_posix(), group, digest, label_text, class_ids)
        if digest in unique:
            previous = unique[digest]
            if previous.label_text != item.label_text:
                raise ValueError(f"重复图片的标注不一致：{previous.relative} 与 {item.relative}")
            first, second = sorted((group_root(previous.group), group_root(group)))
            parents[second] = first
            duplicates += 1
        else:
            images.append(item)
            unique[digest] = item
        if len(seen_labels) > MAX_IMAGES:
            raise ValueError(f"每次最多载入 {MAX_IMAGES} 张图片")
        if progress:
            progress(f"已检查 {len(seen_labels)} 张图片")
    if not images:
        raise ValueError("未找到标注图片；请按导入说明放置数据")
    label_roots = {label_root, image_root} if flat_layout else {label_root}
    for directory in label_roots:
        for path in directory.rglob("*.txt"):
            _check_stop(stop)
            _inside(root, path)
            if path.resolve() == classes_path.resolve():
                continue
            if path.name == "classes.txt" and read_classes(path) == names:
                continue
            if path.relative_to(directory) not in seen_labels:
                raise ValueError(f"标签没有对应图片：{path.relative_to(root)}")
    return AnnotationScan(
        root,
        classes_path.resolve(),
        names,
        tuple(replace(item, group=group_root(item.group)) for item in images),
        duplicates,
        len(parents),
        profile,
        profile_from_images,
    )


def plan_split(scan: AnnotationScan, validation_percent: int = 20, seed: int = 42) -> DatasetPlan:
    if type(validation_percent) is not int or not 1 <= validation_percent <= 99:
        raise ValueError("验证集比例必须为 1–99 的整数百分比")
    if type(seed) is not int or not 0 <= seed <= 2**32 - 1:
        raise ValueError("随机种子必须为 0–4294967295 的整数")
    groups = sorted({item.group for item in scan.images})
    if len(groups) < 2:
        raise ValueError("至少需要两场独立对局；重复画面关联的场次会合并，不能拆到训练与验证两边")
    random.Random(seed).shuffle(groups)
    counts = Counter(item.group for item in scan.images)
    # Bitset subset-sum finds the closest image count while keeping groups intact.
    # Each reachable sum is recorded once, with at most MAX_IMAGES predecessor entries.
    reachable = 1
    previous = [None] * (len(scan.images) + 1)
    for group in groups:
        shifted = reachable << counts[group]
        added = shifted & ~reachable
        while added:
            bit = added & -added
            subtotal = bit.bit_length() - 1
            previous[subtotal] = (subtotal - counts[group], group)
            added ^= bit
        reachable |= shifted
    subtotal = min(
        (value for value in range(1, len(scan.images)) if previous[value] is not None),
        key=lambda value: (abs(value * 100 - len(scan.images) * validation_percent), value),
    )
    val_groups = set()
    while subtotal:
        predecessor = previous[subtotal]
        assert predecessor is not None
        subtotal, group = predecessor
        val_groups.add(group)
    train = tuple(item for item in scan.images if item.group not in val_groups)
    val = tuple(item for item in scan.images if item.group in val_groups)
    warnings = []
    if scan.duplicates:
        warnings.append(f"已合并 {scan.duplicates} 张重复画面；关联场次保持同组。")
    for title, items in (("训练集", train), ("验证集", val)):
        present = {class_id for item in items for class_id in item.classes}
        absent = [name for index, name in enumerate(scan.names) if index not in present]
        if absent:
            warnings.append(f"{title}缺少类别：{'、'.join(absent)}。建议补充对应场次。")
    return DatasetPlan(scan, validation_percent, seed, train, val, tuple(warnings))


def describe_plan(plan: DatasetPlan) -> str:
    lines = [
        f"载入：{len(plan.scan.images)} 张有效图片，{plan.scan.source_groups} 个场次目录，{len(plan.scan.names)} 类",
        f"训练集：{len(plan.train)} 张 / {len({item.group for item in plan.train})} 组",
        f"验证集：{len(plan.val)} 张 / {len({item.group for item in plan.val})} 组（实际 {len(plan.val) / len(plan.scan.images):.1%}）",
        f"目标验证比例 {plan.validation_percent}%；按整场分配，实际比例可能不同。",
        "重复画面按像素去重；同一场的所有图片始终位于同一集合。",
        (
            "训练输入：完整 720×1280 截图，不裁剪；导出时保留输入配置。"
            if plan.scan.input_profile
            else "未发现完整截图输入配置：按旧数据方式导出，无法确认训练与推理输入一致；建议使用「导出图片」。"
        ),
        "",
        "类别目标数（训练 / 验证）：",
    ]
    train_counts = Counter(class_id for item in plan.train for class_id in item.classes)
    val_counts = Counter(class_id for item in plan.val for class_id in item.classes)
    lines.extend(
        f"{index}: {name}  {train_counts[index]} / {val_counts[index]}" for index, name in enumerate(plan.scan.names)
    )
    if plan.warnings:
        lines.extend(("", *plan.warnings))
    return "\n".join(lines)


def export_split(plan: DatasetPlan, destination: Path, *, stop: Event | None = None, progress=None) -> Path:
    """Commit only complete datasets. All output names are generated locally."""
    destination = destination.resolve()
    expected_profile = None if plan.scan.profile_from_images else plan.scan.input_profile
    if _read_input_profile(plan.scan.root) != expected_profile:
        raise ValueError("完整截图输入配置在载入后发生变化，请重新载入")
    if read_classes(plan.scan.classes_path) != plan.scan.names:
        raise ValueError("类别表在载入后发生变化，请重新载入")
    if destination.is_relative_to(plan.scan.root):
        raise ValueError("输出目录不能位于导入目录内部，请选择独立目录")
    _check_stop(stop)
    destination.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=".preparing-", dir=destination))
    final = destination / staging.name.replace(".preparing-", "dataset-", 1)
    try:
        mapping = []
        for split, items in (("train", plan.train), ("val", plan.val)):
            (staging / "images" / split).mkdir(parents=True)
            (staging / "labels" / split).mkdir(parents=True)
            for item in items:
                _check_stop(stop)
                content = _read_limited(_inside(plan.scan.root, item.image), MAX_IMAGE_BYTES)
                label_text, _class_ids = read_labels(_inside(plan.scan.root, item.label), plan.scan.names)
                if _image_digest(content) != item.digest or label_text != item.label_text:
                    raise ValueError(f"数据在载入后发生变化，请重新载入：{item.relative}")
                name = f"{len(mapping):06d}-{item.digest[:12]}{item.image.suffix.lower()}"
                (staging / "images" / split / name).write_bytes(content)
                (staging / "labels" / split / Path(name).with_suffix(".txt")).write_text(label_text, encoding="utf-8")
                mapping.append(
                    {"source": item.relative, "group": item.group, "digest": item.digest, "split": split, "image": name}
                )
                if progress:
                    progress(f"已导出 {len(mapping)} / {len(plan.scan.images)} 张图片")
        yaml = [
            f"path: {json.dumps(final.as_posix(), ensure_ascii=False)}",
            "train: images/train",
            "val: images/val",
            "names:",
        ]
        yaml.extend(f"  {index}: {json.dumps(name, ensure_ascii=False)}" for index, name in enumerate(plan.scan.names))
        (staging / "data.yaml").write_text("\n".join(yaml) + "\n", encoding="utf-8")
        (staging / "classes.txt").write_text("\n".join(plan.scan.names) + "\n", encoding="utf-8")
        if plan.scan.input_profile:
            atomic_json(staging / INPUT_CONFIG_NAME, plan.scan.input_profile)
        (staging / "划分报告.txt").write_text(describe_plan(plan), encoding="utf-8")
        atomic_json(
            staging / "split.json",
            {
                "schema": "crx-yolo-split/v1",
                "source": str(plan.scan.root),
                "seed": plan.seed,
                "validation_percent": plan.validation_percent,
                "classes": list(plan.scan.names),
                "recommended_model": "yolo26n.pt",
                "duplicates_removed": plan.scan.duplicates,
                "images": mapping,
            },
        )
        _check_stop(stop)
        staging.rename(final)
        return final
    finally:
        # Only remove this operation's private staging directory, never user source/output batches.
        if staging.exists() and staging.resolve().parent == destination and staging.name.startswith(".preparing-"):
            shutil.rmtree(staging)
