"""Supported in-game effects, expressed in panel units (15 means 15%)."""

from echoes import ZERO, add_stats, canonical_echo_name, stat_vector


HSIN_SET = "衔梦照世之心"
HSIN_MAIN = "共鸣回响·天演溯心"
ELECTRO = "导电伤害加成"
ENERGY = "共鸣效率"
SKILL_TYPES = ("普攻伤害加成", "重击伤害加成", "共鸣技能伤害加成",
               "共鸣解放伤害加成", "")
ELEMENTS = ("导电", "热熔", "冷凝", "气动", "衍射", "湮灭")

# (pieces, unconditional stats, stats during the Electro Flare / Unison trigger)
# Moonlit Clouds' 5-piece attack bonus applies to the next character.
SETS = {
    HSIN_SET: (
        (2, ((ELECTRO, 10),), ()),
        (5, (), (("暴击", 15), (ELECTRO, 22.5))),
    ),
    "轻云出月": ((2, ((ENERGY, 10),), ()), (5, (), ())),
    "隐世回光": ((2, (("治疗效果加成", 10),), ()), (5, (), ())),
    "茜染怀想之花": ((2, (("治疗效果加成", 10),), ()), (5, (), ())),
}
FIRST_ECHO = {
    HSIN_MAIN: (((ELECTRO, 10),), ((ELECTRO, 10),)),
    "解形煞": (((ENERGY, 10),), ()),
}


def set_effects(echoes, first):
    groups = {}
    for echo in echoes:
        groups.setdefault(echo.sonata, set()).add(canonical_echo_name(echo.name))
    first_stats = FIRST_ECHO.get(canonical_echo_name(first.name), ((), ()))
    normal, triggered = (stat_vector(stats) for stats in first_stats)
    active = []
    for name, species in groups.items():
        for count, always, on_trigger in SETS.get(name, ()):
            if len(species) >= count:
                normal = add_stats(normal, stat_vector(always))
                triggered = add_stats(triggered, stat_vector(on_trigger))
                active.append(f"{name} {count} 件")
    return normal, triggered, tuple(active)


def maximum_bonuses(echoes):
    # Overestimation is intentional. An actual build can use at most one first Echo.
    bonus = ZERO
    names = {canonical_echo_name(echo.name) for echo in echoes}
    sets = {echo.sonata for echo in echoes}
    for name in names & FIRST_ECHO.keys():
        for stats in FIRST_ECHO[name]:
            bonus = add_stats(bonus, stat_vector(stats))
    for name in sets & SETS.keys():
        for _, normal, triggered in SETS[name]:
            bonus = add_stats(bonus, stat_vector(normal + triggered))
    return bonus
