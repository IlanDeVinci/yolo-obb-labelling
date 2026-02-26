from __future__ import annotations

import math
import random
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageEnhance, ImageFilter

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".bmp", ".tiff", ".tif", ".webp"}


@dataclass
class ParsedLabel:
    class_idx: int
    points: list[float]
    fmt: str


@dataclass
class AugmentationOptions:
    brightness_enabled: bool = False
    brightness_count: int = 2
    brightness_strength: float = 0.35
    safe_crop_enabled: bool = False
    safe_crop_count: int = 1
    safe_crop_max_ratio: float = 0.2
    cutout_enabled: bool = False
    cutout_count: int = 1
    cutout_min_objects: int = 1
    cutout_max_objects: int = 6
    cutout_effect_strength: float = 0.35
    background_objects_dir: str = ""


def generate_split_augmentations(
    images_dir: Path,
    labels_dir: Path,
    options: AugmentationOptions,
) -> int:
    pairs = _collect_pairs(images_dir, labels_dir)
    if not pairs:
        return 0

    created = 0
    source_image_pool = [img for img, _ in pairs]
    external_pool = _collect_external_images(options.background_objects_dir)
    background_pool = source_image_pool + external_pool

    for image_path, label_path in pairs:
        try:
            image = Image.open(image_path).convert("RGB")
            labels = _parse_labels(label_path)
        except Exception:
            continue

        if options.brightness_enabled and options.brightness_count > 0:
            for idx in range(options.brightness_count):
                bright = _brightness_factor(idx, options.brightness_count, options.brightness_strength)
                variant = ImageEnhance.Brightness(image).enhance(bright)
                variant = _apply_global_effects(variant, options.cutout_effect_strength * 0.35)
                created += _save_variant(
                    variant,
                    labels,
                    image_path,
                    labels_dir,
                    tag=f"bri{idx + 1}",
                )

        if options.safe_crop_enabled and options.safe_crop_count > 0:
            for idx in range(options.safe_crop_count):
                crop_result = _safe_crop(image, labels, options.safe_crop_max_ratio)
                if crop_result is None:
                    continue
                crop_img, crop_labels = crop_result
                crop_img = _apply_global_effects(crop_img, options.cutout_effect_strength * 0.25)
                created += _save_variant(
                    crop_img,
                    crop_labels,
                    image_path,
                    labels_dir,
                    tag=f"crop{idx + 1}",
                )

        if options.cutout_enabled and options.cutout_count > 0 and labels:
            for idx in range(options.cutout_count):
                comp_result = _cutout_composite(
                    source_image=image,
                    source_labels=labels,
                    all_image_paths=background_pool,
                    external_object_paths=external_pool,
                    min_objects=options.cutout_min_objects,
                    max_objects=options.cutout_max_objects,
                    effect_strength=options.cutout_effect_strength,
                )
                if comp_result is None:
                    continue
                comp_img, comp_labels = comp_result
                created += _save_variant(
                    comp_img,
                    comp_labels,
                    image_path,
                    labels_dir,
                    tag=f"mix{idx + 1}",
                )

    return created


def _collect_pairs(images_dir: Path, labels_dir: Path) -> list[tuple[Path, Path]]:
    pairs: list[tuple[Path, Path]] = []
    if not images_dir.is_dir() or not labels_dir.is_dir():
        return pairs

    for image_path in sorted(images_dir.iterdir()):
        if not image_path.is_file() or image_path.suffix.lower() not in IMAGE_EXTENSIONS:
            continue
        label_path = labels_dir / f"{image_path.stem}.txt"
        if label_path.exists() and label_path.stat().st_size > 0:
            pairs.append((image_path, label_path))
    return pairs


def _collect_external_images(folder: str) -> list[Path]:
    root = Path(folder).expanduser() if folder else None
    if root is None or not root.is_dir():
        return []
    found: list[Path] = []
    for p in root.rglob("*"):
        if p.is_file() and p.suffix.lower() in IMAGE_EXTENSIONS:
            found.append(p)
    return found


def _parse_labels(label_path: Path) -> list[ParsedLabel]:
    labels: list[ParsedLabel] = []
    for line in label_path.read_text(encoding="utf-8").splitlines():
        parts = line.strip().split()
        if len(parts) < 5:
            continue
        try:
            class_idx = int(parts[0])
            values = [float(x) for x in parts[1:]]
        except ValueError:
            continue

        if len(values) >= 8:
            pts = values[:8]
            labels.append(ParsedLabel(class_idx=class_idx, points=pts, fmt="obb"))
        elif len(values) >= 4:
            x_center, y_center, width, height = values[:4]
            x1 = x_center - (width / 2)
            y1 = y_center - (height / 2)
            x2 = x_center + (width / 2)
            y2 = y_center - (height / 2)
            x3 = x_center + (width / 2)
            y3 = y_center + (height / 2)
            x4 = x_center - (width / 2)
            y4 = y_center + (height / 2)
            labels.append(
                ParsedLabel(
                    class_idx=class_idx,
                    points=[x1, y1, x2, y2, x3, y3, x4, y4],
                    fmt="bbox",
                )
            )
    return labels


def _labels_to_lines(labels: list[ParsedLabel]) -> list[str]:
    lines: list[str] = []
    for lbl in labels:
        pts = [_clamp01(v) for v in lbl.points]
        if lbl.fmt == "bbox":
            xs = [pts[0], pts[2], pts[4], pts[6]]
            ys = [pts[1], pts[3], pts[5], pts[7]]
            x_min, x_max = min(xs), max(xs)
            y_min, y_max = min(ys), max(ys)
            x_center = (x_min + x_max) / 2
            y_center = (y_min + y_max) / 2
            width = max(1e-6, x_max - x_min)
            height = max(1e-6, y_max - y_min)
            lines.append(
                f"{lbl.class_idx} {x_center:.6f} {y_center:.6f} {width:.6f} {height:.6f}"
            )
        else:
            coords = " ".join(f"{v:.6f}" for v in pts)
            lines.append(f"{lbl.class_idx} {coords}")
    return lines


def _save_variant(
    image: Image.Image,
    labels: list[ParsedLabel],
    source_image_path: Path,
    labels_dir: Path,
    tag: str,
) -> int:
    if not labels:
        return 0

    stem = source_image_path.stem
    ext = source_image_path.suffix.lower() if source_image_path.suffix else ".jpg"
    if ext not in IMAGE_EXTENSIONS:
        ext = ".jpg"

    candidate = source_image_path.parent / f"{stem}__aug_{tag}{ext}"
    seq = 2
    while candidate.exists():
        candidate = source_image_path.parent / f"{stem}__aug_{tag}_{seq}{ext}"
        seq += 1

    image.save(candidate, quality=95)
    label_path = labels_dir / f"{candidate.stem}.txt"
    lines = _labels_to_lines(labels)
    label_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return 1


def _brightness_factor(index: int, total: int, strength: float) -> float:
    strength = min(0.85, max(0.05, strength))
    if total == 1:
        return random.choice([max(0.2, 1.0 - strength), 1.0 + strength])
    t = index / max(1, total - 1)
    return (1.0 - strength) + (2.0 * strength * t)


def _safe_crop(
    image: Image.Image,
    labels: list[ParsedLabel],
    max_ratio: float,
) -> tuple[Image.Image, list[ParsedLabel]] | None:
    width, height = image.size
    max_ratio = min(0.45, max(0.01, max_ratio))

    if labels:
        x_min_px = width
        y_min_px = height
        x_max_px = 0.0
        y_max_px = 0.0
        for lbl in labels:
            px = [_clamp01(lbl.points[i]) * width for i in range(0, 8, 2)]
            py = [_clamp01(lbl.points[i]) * height for i in range(1, 8, 2)]
            x_min_px = min(x_min_px, min(px))
            x_max_px = max(x_max_px, max(px))
            y_min_px = min(y_min_px, min(py))
            y_max_px = max(y_max_px, max(py))

        left_cap = max(0, int(math.floor(x_min_px)))
        top_cap = max(0, int(math.floor(y_min_px)))
        right_cap = max(0, int(math.floor(width - x_max_px)))
        bottom_cap = max(0, int(math.floor(height - y_max_px)))
    else:
        left_cap = int(width * max_ratio)
        top_cap = int(height * max_ratio)
        right_cap = int(width * max_ratio)
        bottom_cap = int(height * max_ratio)

    left = random.randint(0, min(left_cap, int(width * max_ratio))) if left_cap > 0 else 0
    top = random.randint(0, min(top_cap, int(height * max_ratio))) if top_cap > 0 else 0
    right = random.randint(0, min(right_cap, int(width * max_ratio))) if right_cap > 0 else 0
    bottom = random.randint(0, min(bottom_cap, int(height * max_ratio))) if bottom_cap > 0 else 0

    new_w = width - left - right
    new_h = height - top - bottom
    if new_w < 32 or new_h < 32:
        return None

    cropped = image.crop((left, top, left + new_w, top + new_h))
    out_labels: list[ParsedLabel] = []
    for lbl in labels:
        new_points: list[float] = []
        for i in range(0, 8, 2):
            x_px = _clamp01(lbl.points[i]) * width
            y_px = _clamp01(lbl.points[i + 1]) * height
            nx = (x_px - left) / new_w
            ny = (y_px - top) / new_h
            new_points.extend([_clamp01(nx), _clamp01(ny)])
        out_labels.append(ParsedLabel(class_idx=lbl.class_idx, points=new_points, fmt=lbl.fmt))
    return cropped, out_labels


def _cutout_composite(
    source_image: Image.Image,
    source_labels: list[ParsedLabel],
    all_image_paths: list[Path],
    external_object_paths: list[Path],
    min_objects: int,
    max_objects: int,
    effect_strength: float,
) -> tuple[Image.Image, list[ParsedLabel]] | None:
    if not source_labels:
        return None

    width, height = source_image.size
    bg = _random_background(width, height, all_image_paths)
    _paste_random_distractors(bg, external_object_paths, effect_strength)
    composed_labels: list[ParsedLabel] = []

    k_min = max(1, min(min_objects, len(source_labels)))
    k_max = max(k_min, min(max_objects, len(source_labels)))
    count = random.randint(k_min, k_max)
    chosen = random.sample(source_labels, count)

    occupied: list[tuple[float, float, float, float]] = []

    for lbl in chosen:
        patch_data = _extract_label_patch(source_image, lbl)
        if patch_data is None:
            continue
        patch, mask, rel_points = patch_data

        scale = random.uniform(0.7, 1.25)
        new_w = max(8, int(round(patch.width * scale)))
        new_h = max(8, int(round(patch.height * scale)))
        patch = patch.resize((new_w, new_h), Image.Resampling.BICUBIC)
        mask = mask.resize((new_w, new_h), Image.Resampling.BICUBIC)

        patch = _apply_patch_effects(patch, effect_strength)

        placed = False
        for _ in range(30):
            if new_w >= width or new_h >= height:
                break
            x0 = random.randint(0, width - new_w)
            y0 = random.randint(0, height - new_h)
            x1 = x0 + new_w
            y1 = y0 + new_h
            if _has_heavy_overlap((x0, y0, x1, y1), occupied):
                continue

            bg.paste(patch, (x0, y0), mask)
            occupied.append((x0, y0, x1, y1))

            new_points: list[float] = []
            for i in range(0, 8, 2):
                rx = rel_points[i]
                ry = rel_points[i + 1]
                px = x0 + (rx * new_w)
                py = y0 + (ry * new_h)
                new_points.extend([_clamp01(px / width), _clamp01(py / height)])

            composed_labels.append(
                ParsedLabel(class_idx=lbl.class_idx, points=new_points, fmt=lbl.fmt)
            )
            placed = True
            break

        if not placed:
            continue

    if not composed_labels:
        return None

    bg = _apply_global_effects(bg, effect_strength)
    return bg, composed_labels


def _paste_random_distractors(
    background: Image.Image,
    object_paths: list[Path],
    effect_strength: float,
) -> None:
    if not object_paths:
        return

    width, height = background.size
    distractor_count = random.randint(0, 3)
    for _ in range(distractor_count):
        src_path = random.choice(object_paths)
        try:
            obj = Image.open(src_path).convert("RGB")
        except Exception:
            continue

        src_w, src_h = obj.size
        if src_w < 12 or src_h < 12:
            continue

        scale = random.uniform(0.12, 0.42)
        target_w = max(8, int(width * scale))
        target_h = max(8, int(src_h * (target_w / src_w)))
        if target_w >= width or target_h >= height:
            continue

        obj = obj.resize((target_w, target_h), Image.Resampling.BICUBIC)
        obj = _apply_patch_effects(obj, max(0.12, min(0.85, effect_strength)))

        x0 = random.randint(0, width - target_w)
        y0 = random.randint(0, height - target_h)
        alpha = int(random.uniform(120, 230))

        mask = Image.new("L", (target_w, target_h), color=alpha)
        if random.random() < 0.45:
            mask = mask.filter(ImageFilter.GaussianBlur(radius=random.uniform(0.6, 2.0)))
        background.paste(obj, (x0, y0), mask)


def _extract_label_patch(
    image: Image.Image,
    label: ParsedLabel,
) -> tuple[Image.Image, Image.Image, list[float]] | None:
    width, height = image.size
    px = [_clamp01(label.points[i]) * width for i in range(0, 8, 2)]
    py = [_clamp01(label.points[i]) * height for i in range(1, 8, 2)]

    x0 = int(max(0, math.floor(min(px))))
    y0 = int(max(0, math.floor(min(py))))
    x1 = int(min(width, math.ceil(max(px))))
    y1 = int(min(height, math.ceil(max(py))))

    if x1 - x0 < 4 or y1 - y0 < 4:
        return None

    patch = image.crop((x0, y0, x1, y1))
    mask = Image.new("L", (x1 - x0, y1 - y0), 0)
    draw = ImageDraw.Draw(mask)
    poly = [(px[i] - x0, py[i] - y0) for i in range(4)]
    draw.polygon(poly, fill=255)

    rel_points: list[float] = []
    w = max(1, x1 - x0)
    h = max(1, y1 - y0)
    for i in range(4):
        rel_points.extend([(px[i] - x0) / w, (py[i] - y0) / h])

    return patch, mask, rel_points


def _random_background(width: int, height: int, all_image_paths: list[Path]) -> Image.Image:
    bg_mode = random.choice(["solid", "noise", "texture"])
    if bg_mode == "solid":
        color = tuple(int(random.randint(20, 230)) for _ in range(3))
        return Image.new("RGB", (width, height), color=color)

    if bg_mode == "noise":
        arr = np.random.randint(0, 255, size=(height, width, 3), dtype=np.uint8)
        img = Image.fromarray(arr, mode="RGB")
        return img.filter(ImageFilter.GaussianBlur(radius=random.uniform(0.8, 2.2)))

    if all_image_paths:
        random_path = random.choice(all_image_paths)
        try:
            texture = Image.open(random_path).convert("RGB")
            if texture.width < width or texture.height < height:
                scale = max(width / texture.width, height / texture.height)
                texture = texture.resize(
                    (int(texture.width * scale) + 1, int(texture.height * scale) + 1),
                    Image.Resampling.BICUBIC,
                )
            left = random.randint(0, max(0, texture.width - width))
            top = random.randint(0, max(0, texture.height - height))
            crop = texture.crop((left, top, left + width, top + height))
            return crop.filter(ImageFilter.GaussianBlur(radius=random.uniform(0.4, 1.8)))
        except Exception:
            pass

    return Image.new("RGB", (width, height), color=(40, 40, 40))


def _has_heavy_overlap(
    a: tuple[float, float, float, float],
    occupied: list[tuple[float, float, float, float]],
) -> bool:
    ax0, ay0, ax1, ay1 = a
    a_area = max(1.0, (ax1 - ax0) * (ay1 - ay0))

    for bx0, by0, bx1, by1 in occupied:
        ix0 = max(ax0, bx0)
        iy0 = max(ay0, by0)
        ix1 = min(ax1, bx1)
        iy1 = min(ay1, by1)
        if ix1 <= ix0 or iy1 <= iy0:
            continue
        inter = (ix1 - ix0) * (iy1 - iy0)
        if inter / a_area > 0.35:
            return True
    return False


def _apply_patch_effects(image: Image.Image, strength: float) -> Image.Image:
    strength = min(0.95, max(0.05, strength))
    out = image
    if random.random() < 0.8:
        out = ImageEnhance.Brightness(out).enhance(random.uniform(1.0 - strength, 1.0 + strength))
    if random.random() < 0.8:
        out = ImageEnhance.Contrast(out).enhance(random.uniform(1.0 - strength, 1.0 + strength))
    if random.random() < 0.6:
        out = ImageEnhance.Color(out).enhance(random.uniform(1.0 - strength, 1.0 + strength))
    if random.random() < 0.35:
        out = out.filter(ImageFilter.GaussianBlur(radius=random.uniform(0.3, 1.4)))
    return out


def _apply_global_effects(image: Image.Image, strength: float) -> Image.Image:
    strength = min(0.6, max(0.0, strength))
    out = image
    if strength <= 0:
        return out
    if random.random() < 0.7:
        out = ImageEnhance.Brightness(out).enhance(random.uniform(1.0 - strength, 1.0 + strength))
    if random.random() < 0.7:
        out = ImageEnhance.Contrast(out).enhance(random.uniform(1.0 - strength, 1.0 + strength))
    if random.random() < 0.25:
        out = out.filter(ImageFilter.GaussianBlur(radius=random.uniform(0.2, 1.2)))
    return out


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, value))
