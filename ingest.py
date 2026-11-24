"""Write recognition results and diagnostics without retaining screenshots."""

from dataclasses import replace
from enum import Enum
import json
from pathlib import Path
from uuid import uuid4

from echoes import Inventory, UNKNOWN_OWNER
from scanner import scan_image


class ReadResult(Enum):
    SAVED = "saved"
    SKIPPED = "skipped"
    FAILED = "failed"


def icon_signature(icon):
    import numpy as np
    from PIL import Image

    icon = icon.convert("RGB").resize((20, 20), Image.Resampling.LANCZOS)
    hue, saturation, brightness = np.asarray(icon.convert("HSV"), dtype=float).transpose(2, 0, 1)
    weights = saturation * brightness / 65025
    histogram = np.bincount((hue / 16).astype(int).ravel(), weights=weights.ravel(), minlength=16)
    histogram /= max(1, histogram.sum())
    mono = np.asarray(icon.convert("L").resize((9, 9), Image.Resampling.LANCZOS), dtype=float)
    bits = np.r_[np.diff(mono, axis=0).ravel() > 0, np.diff(mono, axis=1).ravel() > 0]
    bits = np.packbits(bits).tobytes().hex()
    return bits, histogram.round(4).tolist()


class IconSignatures:
    def __init__(self, root):
        self.root = root
        self.path = root / "sonata-signatures.json"
        self.data = json.loads(self.path.read_text(encoding="utf-8")) if self.path.is_file() else {}

    def match(self, icon):
        if icon is None or not self.data:
            return ""
        bits, hist = icon_signature(icon)
        distances = []
        for name, examples in self.data.items():
            distance = min(
                (int(bits, 16) ^ int(other, 16)).bit_count() / (4 * len(bits))
                + sum(abs(a - b) for a, b in zip(hist, colors)) * .45
                for other, colors in examples
            )
            distances.append((distance, name))
        distances.sort()
        value, name = distances[0]
        margin = distances[1][0] - value if len(distances) > 1 else 1
        return name if value <= .23 and margin >= .055 else ""

    def add(self, icon, name):
        if icon is None or not name or self.match(icon) == name:
            return
        examples = self.data.setdefault(name, [])
        if len(examples) >= 6:
            return
        examples.append(icon_signature(icon))
        temp = self.path.with_suffix(".tmp")
        temp.write_text(json.dumps(self.data, ensure_ascii=False), encoding="utf-8")
        temp.replace(self.path)

    def migrate(self):
        from PIL import Image

        for path in (self.root / "icons").glob("*.png"):
            name, _, suffix = path.stem.rpartition("_")
            if not name or len(suffix) != 12:
                continue
            with Image.open(path) as image:
                self.add(image, name)
            path.unlink()


class Reader:
    def __init__(self, root):
        self.root = Path(root)
        self.db = Inventory(self.root / "inventory.sqlite3")
        self.icons = IconSignatures(self.root)

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.db.close()

    def record(self, source, record_id=None, *, equipped_hint=False, sonata_hint=""):
        record_id = record_id or uuid4().hex
        try:
            draft = scan_image(source, self.icons, sonata_hint=sonata_hint)
            if 0 <= draft.echo.level < 25:
                return ReadResult.SKIPPED
            owner = draft.echo.owner
            if equipped_hint and not owner:
                owner = UNKNOWN_OWNER
                draft.warnings.append("卡片有角色头像，详情未读到角色名")
            echo = replace(draft.echo, id=record_id, owner=owner, warnings=tuple(draft.warnings),
                           sonata=draft.echo.sonata or "未识别")
            self.db.save(echo)
        except (ValueError, OSError) as error:
            self.db.record_failure(record_id, str(error))
            return ReadResult.FAILED
        if draft.verified_sonata:
            self.icons.add(draft.icon, draft.verified_sonata)
        return ReadResult.SAVED

    def migrate(self, progress=lambda _: None):
        import re

        self.icons.migrate()
        self.db.remove_legacy_image_paths(self.root / "captures")
        pending = sorted((self.root / "pending").glob("*.png"))
        owned = [path for path in pending
                 if re.fullmatch(r"(auto-[a-f0-9]{32}-\d{6}|\d{15,})", path.stem)]
        for index, path in enumerate(owned, 1):
            progress(f"正在识别旧版待处理记录 · {index} / {len(owned)}")
            self.record(path, "capture-" + path.stem)
            path.unlink()
        return len(owned)
