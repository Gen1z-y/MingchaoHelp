"""Read the selected Echo's details from a simplified-Chinese screenshot."""

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
import re
import sys
import unicodedata

from echoes import Echo, PERCENT_STATS, FLAT_STATS, UNKNOWN_OWNER, canonical_echo_name, normalize_name
from profiles import FALLBACK_GUIDES, GUIDES
from rules import SETS


DETAIL_BOUNDS = ((.675, .125, .985, .95), (.785, .13, .992, .955))
NUMBER = re.compile(r"^(\d+(?:\.\d+)?)(%?)$")
NAME = re.compile(r"^[\u3400-\u9fff·]{2,}$")
STAT_LABELS = sorted({s.removesuffix("%") for s in PERCENT_STATS + FLAT_STATS},
                     key=len, reverse=True)
OWNER_NAMES = frozenset((*GUIDES, "漂泊者"))
WORN_LABEL = re.compile(r"(装配中|装备中|已装配)$")
SONATA_NAMES = tuple(sorted(
    set(SETS) | {name for guides in (GUIDES, FALLBACK_GUIDES)
                 for guide in guides.values() for name in guide.sonatas},
    key=lambda name: (-len(name), name),
))


@dataclass
class Line:
    text: str
    confidence: float
    left: float
    right: float
    top: float
    bottom: float

    @property
    def y(self):
        return (self.top + self.bottom) / 2


@dataclass
class Draft:
    echo: Echo
    warnings: list
    icon: object = None
    verified_sonata: str = ""


class SonataMismatch(Exception):
    pass


@lru_cache(maxsize=1)
def ocr_engine():
    if getattr(sys, "frozen", False):
        from rapidocr_onnxruntime import RapidOCR

        engine = RapidOCR(intra_op_num_threads=2, inter_op_num_threads=1)

        def recognize(pixels):
            result, _ = engine(pixels, use_cls=False)
            return ((box, (raw, confidence)) for box, raw, confidence in result or [])

        return recognize

    from paddleocr import PaddleOCR

    models = Path(__file__).resolve().parent / ".ocr-models"
    models.mkdir(exist_ok=True)
    engine = PaddleOCR(
        lang="ch", use_angle_cls=False, use_gpu=False, show_log=False,
        cpu_threads=2, det_model_dir=str(models / "det"),
        rec_model_dir=str(models / "rec"), cls_model_dir=str(models / "cls"),
    )

    def recognize(pixels):
        result = engine.ocr(pixels, cls=False)
        return (result[0] or []) if result else []

    return recognize


def read_lines(image):
    import numpy as np

    pixels = np.array(image.convert("RGB"))[:, :, ::-1].copy()
    lines = []
    for box, (raw, confidence) in ocr_engine()(pixels):
        xs, ys = zip(*box)
        normalized = unicodedata.normalize("NFKC", raw)
        lines.append(Line(normalize_name(normalized), confidence, min(xs), max(xs),
                          min(ys), max(ys)))
    return sorted(lines, key=lambda line: line.y)


def read_stat(label, value):
    number = NUMBER.fullmatch(value)
    if not number:
        return None
    for name in STAT_LABELS:
        if label.endswith(name):
            key = name + "%" if name in FLAT_STATS and number[2] else name
            if (key in PERCENT_STATS) != bool(number[2]):
                return None
            return key, float(number[1])
    return None


def sonata_names(lines):
    found = set()
    for line in lines:
        if line.confidence < .80:
            continue
        text = line.text
        for name in SONATA_NAMES:
            if name in text:
                found.add(name)
                text = text.replace(name, "")
    return frozenset(found)


def read_owner(lines, after_y, typical_height):
    for line in lines:
        if line.y <= after_y or line.text.startswith("在首位") or not WORN_LABEL.search(line.text):
            continue
        name = WORN_LABEL.sub("", line.text)
        if not name:
            preceding = [other for other in lines if other.right < line.left
                         and line.left - other.right < typical_height * 3
                         and abs(other.y - line.y) < typical_height * .55
                         and NAME.fullmatch(other.text)]
            if preceding:
                name = max(preceding, key=lambda other: other.right).text
        if name in OWNER_NAMES:
            return name
        prefix_matches = [known for known in OWNER_NAMES
                          if name and known.startswith(name) and len(known) == len(name) + 1]
        if len(prefix_matches) == 1:
            return prefix_matches[0]
        if len(name) <= 10 and NAME.fullmatch(name) and line.confidence >= .93:
            return name
        return UNKNOWN_OWNER
    return ""


def parse_lines(lines, width):
    cost_line = next((line for line in lines if re.fullmatch(r"COST[134]", line.text)), None)
    if cost_line is None:
        raise ValueError("未找到 COST，请导入显示单件声骸详情的截图")
    heights = [line.bottom - line.top for line in lines if line.confidence >= .75]
    typical_height = sorted(heights)[len(heights) // 2] if heights else 20
    title = next(
        (line for line in reversed(lines) if line.y < cost_line.y
         and abs(line.left - cost_line.left) < width * .14 and NAME.fullmatch(line.text)),
        None,
    )
    if title is None:
        raise ValueError("未识别到声骸名称，请检查右侧详情是否完整")
    level_line = next(
        (line for line in lines if title.top - typical_height < line.y < cost_line.y
         and re.fullmatch(r"\+\d{1,2}", line.text)),
        None,
    )
    skill_y = next((line.y for line in lines if line.y > cost_line.y
                    and line.text.startswith(("声骸技能", "合鸣效果"))), float("inf"))
    labels = [line for line in lines if cost_line.y < line.y < skill_y
              and abs(line.left - cost_line.left) < width * .22]
    values = [line for line in lines if cost_line.y < line.y < skill_y
              and line.right > cost_line.left + width * .35 and NUMBER.fullmatch(line.text)]
    stats = []
    used_values = set()
    warnings = []
    for label in labels:
        joined = re.fullmatch(r"(.+?)(\d+(?:\.\d+)?%?)", label.text)
        nearby = sorted((v for v in values if id(v) not in used_values
                         and abs(v.y - label.y) <= typical_height * .60),
                        key=lambda v: abs(v.y - label.y))
        row = read_stat(joined[1], joined[2]) if joined else None
        if row is None and nearby:
            row = read_stat(label.text, nearby[0].text)
            if row:
                used_values.add(id(nearby[0]))
                if nearby[0].confidence < .87:
                    warnings.append(f"请核对数值 {nearby[0].text}")
        if row:
            stats.append(row)
        elif nearby:
            warnings.append(f"无法配对 {label.text} 与 {nearby[0].text}")
    if len(stats) < 2 or len(stats) > 7:
        raise ValueError(f"识别到 {len(stats)} 条属性，请检查截图是否包含完整详情")
    if any(id(value) not in used_values and value.confidence > .88 for value in values):
        warnings.append("有未匹配的数字，请核对词条顺序")
    if level_line is None:
        warnings.append("等级读取失败，请填写")
    status_start = skill_y if skill_y != float("inf") else cost_line.y
    owner = read_owner(lines, status_start, typical_height)
    if owner == UNKNOWN_OWNER:
        warnings.append("已检测到穿戴状态，角色名未读清")
    sonatas = sonata_names(line for line in lines if line.y > cost_line.y and
                           abs(line.left - cost_line.left) < width * .22)
    sonata = next(iter(sonatas)) if len(sonatas) == 1 else ""
    echo = Echo(canonical_echo_name(title.text), int(cost_line.text[-1]),
                int(level_line.text) if level_line else -1, sonata,
                tuple(stats[:2]), tuple(stats[2:]), owner=owner)
    if title.confidence < .88:
        warnings.append("请核对声骸名称")
    return echo, warnings, level_line


def icon_crop(image, level):
    if level is None:
        return None
    height = max(1, level.bottom - level.top)
    x = level.right + .93 * height
    half = .62 * height
    if x + half > image.width:
        return None
    return image.crop((round(x - half), round(level.y - half),
                       round(x + half), round(level.y + half)))


def scan_image(source, icons, *, sonata_hint=""):
    from PIL import Image, ImageOps
    from dataclasses import replace

    if isinstance(source, Image.Image):
        image = source
    else:
        with Image.open(source) as opened:
            image = ImageOps.exif_transpose(opened).convert("RGB")

    width, height = image.size
    bounds = [tuple(round(value * scale) for value, scale in
                    zip(box, (width, height, width, height))) for box in DETAIL_BOUNDS]
    if width / height < 1.25:
        bounds.insert(0, (0, 0, width, height))
    last_error = None
    for box in bounds:
        preview = image.crop(box)
        factor = 2 if preview.height < 850 else 1
        scaled = preview.resize((preview.width * factor, preview.height * factor),
                                Image.Resampling.BICUBIC)
        try:
            echo, warnings, level = parse_lines(read_lines(scaled), scaled.width)
        except ValueError as error:
            last_error = error
            continue
        if echo.sonata and sonata_hint and echo.sonata != sonata_hint:
            raise SonataMismatch(f"合鸣筛选为「{sonata_hint}」，详情读到「{echo.sonata}」")
        icon = icon_crop(scaled, level)
        verified_sonata = echo.sonata or sonata_hint
        sonata = verified_sonata or icons.match(icon)
        if not sonata:
            warnings.append("合鸣未识别")
        return Draft(replace(echo, sonata=sonata), warnings, icon, verified_sonata)
    raise last_error or ValueError("没有找到可识别的声骸详情")


