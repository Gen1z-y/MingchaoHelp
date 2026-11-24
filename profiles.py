"""Editable character starting points based on published build guides."""

from dataclasses import dataclass
import json
from pathlib import Path

from rules import HSIN_SET


ALL_SONATAS = "不限套装"


@dataclass(frozen=True)
class EchoPreset:
    first: str
    sets: tuple

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

def read_guides(filename):
    with Path(__file__).with_name(filename).open(encoding="utf-8") as snapshot:
        data = json.load(snapshot)
    return {
        name: CharacterGuide(
            element=guide["element"], scaling=guide["scaling"],
            priorities=tuple(guide["priorities"]),
            mains=tuple((cost, tuple(names)) for cost, names in guide["mains"]),
            sonatas=tuple(guide["sonatas"]), url=guide["url"],
            energy=guide.get("energy", 0), crit_rate=guide.get("crit_rate", 0),
            crit_damage=guide.get("crit_damage", 0),
            release_date=guide.get("release_date", ""),
            echo_presets=tuple(EchoPreset(first, tuple((name, count) for name, count in sets))
                               for first, sets in guide.get("echo_presets", ())),
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
        "target_crit_rate": "", "target_crit_damage": "", "target_energy": "",
        "priorities": "", "main4": "", "main3": "", "main1": "",
        "element": "导电", "scaling": "攻击", "ranking": "循环伤害",
        "required_set": ALL_SONATAS,
    }
    if guide:
        defaults.update(
            element=guide.element, scaling=guide.scaling, ranking="推荐词条",
            priorities=" > ".join(guide.priorities),
            target_crit_rate=f"{guide.crit_rate:g}" if guide.crit_rate else "",
            target_crit_damage=f"{guide.crit_damage:g}" if guide.crit_damage else "",
            target_energy=f"{guide.energy:g}" if guide.energy else "",
            required_set=guide.sonata_choices[0] if guide.sonata_choices else ALL_SONATAS,
        )
        for cost, stats in guide.mains:
            defaults[f"main{cost}"] = " / ".join(stats)
    return defaults
