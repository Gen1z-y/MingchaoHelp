"""Editable character starting points based on published build guides."""

from dataclasses import dataclass
from decimal import Decimal
import json
from pathlib import Path
import re

from echoes import PERCENT_STATS
from rules import ADDITIONAL_TARGETS, HSIN_SET


ALL_SONATAS = "不限套装"


@dataclass(frozen=True)
class EchoPreset:
    first: str
    sets: tuple
    main_slots: tuple = ()

    @property
    def label(self):
        if len(self.sets) == 1 and self.sets[0][1] == 5:
            return self.sets[0][0]
        return " + ".join(f"{name} {count}件" for name, count in self.sets)


@dataclass(frozen=True)
class CharacterGuide:
    element: str
    scaling: str
    priorities: tuple
    mains: tuple
    sonatas: tuple
    url: str
    energy: int = 0
    crit_rate: float = 0
    crit_damage: float = 0
    release_date: str = ""
    echo_presets: tuple = ()
    attack: float = 0
    additional_targets: tuple = ()
    catalog_order: int = 0
    weapon_type: str = ""
    base_stats: dict | None = None
    skill_bonuses: tuple | None = None

    @property
    def sonata_choices(self):
        return tuple(dict.fromkeys(preset.label for preset in self.echo_presets)) or self.sonatas

    def echo_setup(self, selected):
        presets = [preset for preset in self.echo_presets if preset.label == selected]
        if selected == ALL_SONATAS:
            firsts = tuple(dict.fromkeys(preset.first for preset in self.echo_presets))
            return (), firsts
        if presets:
            firsts = tuple(dict.fromkeys(preset.first for preset in presets))
            return presets[0].sets, firsts
        return ((selected, 5),), ()

    def main_layouts(self, selected):
        presets = [preset for preset in self.echo_presets if preset.label == selected]
        use_first = bool(presets) or selected == ALL_SONATAS
        return tuple((preset.first if use_first else "", preset.main_slots)
                     for preset in presets or self.echo_presets if preset.main_slots)


PREVIOUS_PRESETS = {
    "锁暝": CharacterGuide(
        "导电", "攻击", ("共鸣效率", "暴击", "暴击伤害", "攻击%", "普攻伤害加成"),
        ((4, ("暴击伤害", "暴击")), (3, ("导电伤害加成",)), (1, ("攻击%",))),
        (HSIN_SET, "轻云出月"),
        "https://mcguide.kurogames.com/zh-Hans/?channelId_cloud=211&client_entry=0"
        "&packageId_cloud=A1493&role_id=1312&strategy_id=10233", 120,
    ),
    "莉莉": CharacterGuide(
        "衍射", "生命", ("共鸣效率", "生命%", "生命", "暴击伤害", "重击伤害加成"),
        ((4, ("治疗效果加成",)), (3, ("共鸣效率",)), (1, ("生命%",))),
        ("茜染怀想之花", "隐世回光"), "https://mcguide.kurogames.com/", 260,
    ),
    "守岸人": CharacterGuide(
        "衍射", "生命", ("共鸣效率", "生命%", "生命", "治疗效果加成"),
        ((4, ("治疗效果加成", "生命%")), (3, ("共鸣效率",)), (1, ("生命%",))),
        ("隐世回光",), "https://wuwa.huijiwiki.com/wiki/守岸人/数据", 250,
    ),
}

def parse_skill_bonuses(nodes):
    if not isinstance(nodes, list):
        return None
    totals = {}
    for node in nodes:
        title = node.get("PropertyNodeTitle", "").strip()
        name = title.removesuffix("提升")
        if name in {"攻击", "生命", "防御"}:
            name += "%"
        desc = node.get("PropertyNodeDescribe", "").strip()
        match = re.fullmatch(re.escape(title) + r"\s*(\d+(?:\.\d+)?)%", desc)
        if name not in PERCENT_STATS or not match:
            return None
        totals[name] = totals.get(name, Decimal(0)) + Decimal(match[1])
    return {name: float(value) for name, value in sorted(totals.items())}


def read_guides(filename):
    with Path(__file__).with_name(filename).open(encoding="utf-8") as snapshot:
        return parse_guides(json.load(snapshot))


def parse_guides(data):
    return {
        name: CharacterGuide(
            element=guide["element"], scaling=guide["scaling"],
            priorities=tuple(guide["priorities"]),
            mains=tuple((cost, tuple(names)) for cost, names in guide["mains"]),
            sonatas=tuple(guide["sonatas"]), url=guide["url"],
            energy=guide.get("energy", 0), crit_rate=guide.get("crit_rate", 0),
            crit_damage=guide.get("crit_damage", 0),
            attack=guide.get("attack", 0),
            additional_targets=tuple(guide.get("additional_targets", {}).items()),
            release_date=guide.get("release_date", ""),
            catalog_order=guide.get("catalog_order", 0),
            weapon_type=guide.get("weapon_type", ""),
            base_stats=guide.get("base_stats"),
            skill_bonuses=(tuple(guide["skill_bonuses"].items())
                           if guide.get("skill_bonuses") is not None else None),
            echo_presets=tuple(
                EchoPreset(first, tuple((name, count) for name, count in sets),
                           tuple((cost, stat) for cost, stat in (slots[0] if slots else ())))
                for first, sets, *slots in guide.get("echo_presets", ())
            ),
        )
        for name, guide in data.items()
    }


FALLBACK_GUIDES = read_guides("character-guides.json")
PREVIOUS_GUIDES = FALLBACK_GUIDES | PREVIOUS_PRESETS
OFFICIAL_GUIDES = read_guides("kuro-character-guides.json")
GUIDES = FALLBACK_GUIDES | OFFICIAL_GUIDES
ROLE_ORDER = tuple(sorted(GUIDES, key=lambda name: GUIDES[name].release_date, reverse=True))


def guide_defaults(guide):
    defaults = {
        "target_crit_rate": "", "target_crit_damage": "", "target_energy": "", "target_attack": "",
        "priorities": "", "main4": "", "main3": "", "main1": "",
        "element": "导电", "scaling": "攻击", "ranking": "循环伤害",
        "required_set": ALL_SONATAS,
    }
    defaults.update(dict.fromkeys(ADDITIONAL_TARGETS, ""))
    if guide:
        defaults.update(
            element=guide.element, scaling=guide.scaling,
            ranking="推荐词条" if guide.priorities else "循环伤害",
            priorities=" > ".join(guide.priorities),
            target_crit_rate=f"{guide.crit_rate:g}" if guide.crit_rate else "",
            target_crit_damage=f"{guide.crit_damage:g}" if guide.crit_damage else "",
            target_energy=f"{guide.energy:g}" if guide.energy else "",
            target_attack=f"{guide.attack:g}" if guide.attack else "",
            required_set=guide.sonata_choices[0] if guide.sonata_choices else ALL_SONATAS,
        )
        for cost, stats in guide.mains:
            defaults[f"main{cost}"] = " / ".join(stats)
        additional = dict(guide.additional_targets)
        for key, name in ADDITIONAL_TARGETS.items():
            if additional.get(name):
                defaults[key] = f"{additional[name]:g}"
    return defaults
