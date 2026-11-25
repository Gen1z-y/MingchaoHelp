from dataclasses import dataclass, field
from hashlib import sha256
import math
import sqlite3
import json
from pathlib import Path
from uuid import uuid4


PERCENT_STATS = (
    "攻击%", "生命%", "防御%", "暴击", "暴击伤害", "共鸣效率", "治疗效果加成",
    "普攻伤害加成", "重击伤害加成", "共鸣技能伤害加成", "共鸣解放伤害加成",
    "导电伤害加成", "热熔伤害加成", "冷凝伤害加成", "气动伤害加成",
    "衍射伤害加成", "湮灭伤害加成",
)
FLAT_STATS = ("攻击", "生命", "防御")
STAT_NAMES = PERCENT_STATS + FLAT_STATS
STAT_INDEX = {name: index for index, name in enumerate(STAT_NAMES)}
ZERO = (0.0,) * len(STAT_NAMES)
UNKNOWN_OWNER = "已穿戴，角色未识别"
ECHO_NAME_CORRECTIONS = {"天愧劫煞": "天傀劫煞"}


def stat_vector(stats):
    values = list(ZERO)
    for name, value in stats:
        if name not in STAT_INDEX or not math.isfinite(value) or value < 0:
            raise ValueError(f"无效词条 {name} {value}")
        index = STAT_INDEX[name]
        values[index] += value
    return tuple(values)


def add_stats(left, right):
    return tuple(a + b for a, b in zip(left, right))


def normalize_name(name):
    return name.strip().replace(" ", "").replace("・", "·").replace("•", "·")


def canonical_echo_name(name):
    normalized = normalize_name(name)
    return ECHO_NAME_CORRECTIONS.get(normalized, normalized)


@dataclass(frozen=True)
class Echo:
    name: str
    cost: int
    level: int
    sonata: str
    main: tuple
    sub: tuple
    owner: str = ""
    locked: bool = False
    warnings: tuple = ()
    id: str = field(default_factory=lambda: uuid4().hex)

    def validate(self):
        if not self.name.strip() or not self.sonata.strip():
            raise ValueError("请确认声骸名称和合鸣套装")
        if self.cost not in {1, 3, 4} or not 0 <= self.level <= 25:
            raise ValueError("COST 应为 1、3 或 4，等级应在 0 至 25")
        if len(self.main) != 2 or len(self.sub) > 5:
            raise ValueError("请填写两条主属性和最多五条已解锁副词条")
        if len({name for name, _ in self.sub}) != len(self.sub):
            raise ValueError("副词条重复，请核对固定值和百分比")
        stat_vector(self.main + self.sub)

    @property
    def needs_review(self):
        return bool(self.warnings) or self.sonata == "未识别" or self.owner == UNKNOWN_OWNER

    def stats(self):
        return stat_vector(self.main + self.sub)


class Inventory:
    def __init__(self, path):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self.cards = path.parent / "cards"
        self.db = sqlite3.connect(path, timeout=10)
        self.db.execute("CREATE TABLE IF NOT EXISTS echoes (id TEXT PRIMARY KEY, data TEXT NOT NULL)")
        self.db.execute("CREATE TABLE IF NOT EXISTS recognition_failures (id TEXT PRIMARY KEY, message TEXT NOT NULL)")
        self.db.execute("CREATE TABLE IF NOT EXISTS deleted_cards (id TEXT PRIMARY KEY)")

    def card_path(self, echo_id):
        return self.cards / (sha256(echo_id.encode("utf-8")).hexdigest() + ".webp")

    def save(self, echo):
        echo.validate()
        data = {**vars(echo), "name": canonical_echo_name(echo.name)}
        with self.db:
            self.db.execute(
                "INSERT INTO echoes VALUES (?, ?) ON CONFLICT(id) DO UPDATE SET data=excluded.data",
                (echo.id, json.dumps(data, ensure_ascii=False)),
            )
            self.db.execute("DELETE FROM recognition_failures WHERE id = ?", (echo.id,))
            self.db.execute("DELETE FROM deleted_cards WHERE id = ?", (echo.id,))

    def update_echo(self, previous, edited):
        if previous.id != edited.id:
            raise ValueError("修正必须保留原声骸记录")
        edited.validate()
        self.db.execute("BEGIN IMMEDIATE")
        with self.db:
            row = self.db.execute("SELECT data FROM echoes WHERE id = ?", (previous.id,)).fetchone()
            if not row or self.decode_echo(row[0]) != previous:
                raise ValueError("这条声骸已变更，请刷新后重新编辑")
            data = json.loads(row[0])
            data.update(vars(edited), name=canonical_echo_name(edited.name))
            self.db.execute("UPDATE echoes SET data = ? WHERE id = ?",
                            (json.dumps(data, ensure_ascii=False), edited.id))

    def equip(self, owner, echoes):
        expected_owners = {echo.id: echo.owner for echo in echoes}
        if not owner.strip() or owner in {UNKNOWN_OWNER, "装配中（待确认）"} or len(expected_owners) != 5:
            raise ValueError("请选择有效角色和五件不同的声骸")
        ids = tuple(expected_owners)
        slots = ", ".join("?" for _ in ids)
        self.db.execute("BEGIN IMMEDIATE")
        with self.db:
            actual_owners = dict(self.db.execute(
                f"SELECT id, coalesce(json_extract(data, '$.owner'), '') FROM echoes WHERE id IN ({slots})",
                ids,
            ))
            if actual_owners.keys() != expected_owners.keys() or any(
                actual not in (expected_owners[echo_id], owner) for echo_id, actual in actual_owners.items()
            ):
                raise ValueError("声骸库存或穿戴角色已变更，请重新计算搭配")
            self.db.execute(
                f"UPDATE echoes SET data = json_set(data, '$.owner', '') "
                f"WHERE json_extract(data, '$.owner') = ? AND id NOT IN ({slots})",
                (owner, *ids),
            )
            self.db.execute(
                f"UPDATE echoes SET data = json_set(data, '$.owner', ?) WHERE id IN ({slots})",
                (owner, *ids),
            )

    def clear_sonata(self, sonata):
        if not sonata:
            raise ValueError("请选择要清除的合鸣套装")
        with self.db:
            removed = self.db.execute(
                "DELETE FROM echoes WHERE json_extract(data, '$.sonata') = ? RETURNING id",
                (sonata,),
            ).fetchall()
            self.db.executemany(
                "INSERT OR IGNORE INTO deleted_cards VALUES (?)", removed,
            )
        return {echo_id for (echo_id,) in removed}, self.clean_deleted_cards()

    def clean_deleted_cards(self):
        remaining = 0
        self.db.execute("BEGIN IMMEDIATE")
        with self.db:
            for (echo_id,) in self.db.execute("SELECT id FROM deleted_cards").fetchall():
                try:
                    self.card_path(echo_id).unlink(missing_ok=True)
                except OSError:
                    remaining += 1
                else:
                    self.db.execute("DELETE FROM deleted_cards WHERE id = ?", (echo_id,))
        return remaining

    @staticmethod
    def decode_echo(payload):
        data = json.loads(payload)
        data["name"] = canonical_echo_name(data["name"])
        for key in ("main", "sub"):
            data[key] = tuple((name, value) for name, value in data[key])
        data.pop("image", None)
        if data.get("owner") == "装配中（待确认）":
            data["owner"] = UNKNOWN_OWNER
        data["warnings"] = tuple(data.get("warnings", ()))
        return Echo(**data)

    def all(self):
        return [self.decode_echo(payload) for (payload,) in
                self.db.execute("SELECT data FROM echoes ORDER BY rowid")]

    def record_failure(self, record_id, message):
        with self.db:
            self.db.execute("INSERT OR REPLACE INTO recognition_failures VALUES (?, ?)",
                            (record_id, message))

    def failure_count(self):
        return self.db.execute("SELECT count(*) FROM recognition_failures").fetchone()[0]

    def recent_failures(self, limit=100):
        return self.db.execute(
            "SELECT id, message FROM recognition_failures ORDER BY rowid DESC LIMIT ?", (limit,)
        ).fetchall()

    def remove_legacy_image_paths(self, captures):
        for echo_id, payload in self.db.execute("SELECT id, data FROM echoes").fetchall():
            data = json.loads(payload)
            if "image" not in data:
                continue
            path = data.pop("image")
            if path:
                expected = captures / f"{echo_id}.png"
                if expected.parent == captures and Path(path).resolve() == expected.resolve():
                    expected.unlink(missing_ok=True)
            with self.db:
                self.db.execute("UPDATE echoes SET data = ? WHERE id = ?",
                                (json.dumps(data, ensure_ascii=False), echo_id))

    def close(self):
        self.db.close()
