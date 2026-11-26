"""Offline level 90 character and max-level rank 1 weapon data from Encore.moe."""

from dataclasses import dataclass, field
import json
from pathlib import Path

from echoes import stat_vector


NO_WEAPON = "不选武器"


@dataclass(frozen=True)
class Weapon:
    id: str
    name: str
    weapon_type: str
    level: int
    attack: float
    affixes: tuple
    stats: tuple


@dataclass(frozen=True)
class CharacterPanel:
    weapon_type: str
    base_stats: dict
    skill_bonuses: tuple | None = None
    skill_stats: tuple = field(init=False, repr=False)

    def __post_init__(self):
        object.__setattr__(self, "skill_stats", stat_vector(self.skill_bonuses or ()))


with Path(__file__).with_name("weapons.json").open(encoding="utf-8") as snapshot:
    catalog = json.load(snapshot)

WEAPONS = {
    name: Weapon(id, name, kind, level, attack, tuple(affixes.items()), stat_vector(affixes.items()))
    for name, (id, kind, level, attack, affixes) in catalog["weapons"].items()
}
WEAPONS_BY_ID = {weapon.id: weapon for weapon in WEAPONS.values()}
CHARACTER_PANELS = {
    name: CharacterPanel(kind, stats, tuple(bonuses.items()))
    for name, (kind, stats, bonuses) in catalog["characters"].items()
}
