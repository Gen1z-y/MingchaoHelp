from collections import Counter, defaultdict, deque
from dataclasses import dataclass
from itertools import combinations_with_replacement
import math
from time import monotonic

from echoes import STAT_INDEX, UNKNOWN_OWNER, ZERO, add_stats, canonical_echo_name
from rules import ADDITIONAL_TARGETS, ENERGY, FIRST_ECHO, SKILL_TYPES, maximum_bonuses, set_effects
from weapons import CharacterPanel, Weapon


ROLL_REFERENCE = {"暴击": 8, "暴击伤害": 16, ENERGY: 10, "攻击": 45,
                  "防御": 50, "生命": 450, "治疗效果加成": 10}
PANEL_BASES = {"攻击": 1000, "防御": 1000, "生命": 10000}


@dataclass(frozen=True)
class Profile:
    name: str = "心"
    element: str = "导电"
    base: float = 1000
    scaling: str = "攻击"
    extra_flat: float = 0
    extra_percent: float = 0
    crit_rate: float = 5
    crit_damage: float = 150
    elemental_bonus: float = 0
    energy: float = 100
    minimum_energy: float = 100
    # Relative pre-bonus coefficients for normal, heavy, skill, liberation, other.
    # This editable starter profile is an estimate, not a game rotation preset.
    weights: tuple = (0, 0, 45, 40, 15)
    coverage: tuple = (1, 1, 1, 1, 1)
    target_crit_rate: float = 0
    target_crit_damage: float = 0
    target_energy: float = 0
    targets_triggered: bool = False
    priorities: tuple = ()
    main_choices: tuple = ()
    recommended_sets: tuple = ()
    ranking: str = "循环伤害"
    priority_ranks: tuple = ()
    target_attack: float = 0
    additional_targets: tuple = ()
    character: CharacterPanel | None = None
    weapon: Weapon | None = None

    def validate(self):
        if self.character and self.weapon and self.character.weapon_type != self.weapon.weapon_type:
            raise ValueError(f"「{self.name}」只能使用{self.character.weapon_type}")
        values = (self.base, self.extra_flat, self.extra_percent, self.crit_rate,
                  self.crit_damage, self.elemental_bonus, self.energy,
                  self.minimum_energy, self.target_crit_rate, self.target_crit_damage,
                  self.target_energy, self.target_attack, *self.weights, *self.coverage,
                  *(target for _, target in self.additional_targets))
        if (not self.name.strip() or any(not math.isfinite(v) or v < 0 for v in values)
                or self.base <= 0 or self.crit_damage < 100 or not any(self.weights)
                or len(self.weights) != len(SKILL_TYPES) or len(self.coverage) != len(SKILL_TYPES)
                or self.target_crit_rate > 100
                or (self.target_crit_damage and self.target_crit_damage < 100)
                or any(x > 1 for x in self.coverage)
                or self.scaling not in PANEL_BASES
                or any(name not in ADDITIONAL_TARGETS.values() for name, _ in self.additional_targets)
                or len(dict(self.additional_targets)) != len(self.additional_targets)
                or self.element + "伤害加成" not in STAT_INDEX
                or self.ranking not in {"推荐词条", "循环伤害"}
                or (self.ranking == "推荐词条" and not self.priorities)
                or len(set(self.priorities)) != len(self.priorities)
                or (self.priority_ranks and (len(self.priority_ranks) != len(self.priorities)
                    or any(not isinstance(rank, int) or rank < 0 for rank in self.priority_ranks)))
                or any(name not in STAT_INDEX for _, names in self.main_choices for name in names)
                or any(name not in STAT_INDEX for name in self.priorities)):
            raise ValueError("请检查面板、属性目标、技能比例和推荐词条设置")

    def stat_value(self, name, stats):
        index = STAT_INDEX[name]
        return (stats[index] + (self.weapon.stats[index] if self.weapon else 0)
                + (self.character.skill_stats[index] if self.character else 0))

    def total_energy(self, stats):
        return self.energy + self.stat_value(ENERGY, stats)

    def panel_attribute(self, name, stats):
        value = self.stat_value(name, stats)
        if name not in PANEL_BASES:
            return value
        percent = self.stat_value(name + "%", stats)
        base = self.base if name == self.scaling else PANEL_BASES[name]
        if self.character:
            base = self.character.base_stats[name]
        if self.weapon and name == "攻击":
            base += self.weapon.attack
        if name == self.scaling:
            percent += self.extra_percent
            value += self.extra_flat
        return base * (1 + percent / 100) + value

    def panel(self, stats):
        value = lambda name: self.stat_value(name, stats)
        scaling = self.panel_attribute(self.scaling, stats)
        crit = self.character.base_stats["暴击"] if self.character else self.crit_rate
        crit_damage = self.character.base_stats["暴击伤害"] if self.character else self.crit_damage
        return (scaling, crit + value("暴击"), crit_damage + value("暴击伤害"),
                self.total_energy(stats), self.elemental_bonus + value(self.element + "伤害加成"))

    def scores(self, stats):
        scaling, crit, crit_damage, _, element = self.panel(stats)
        crit_factor = 1 + min(1, crit / 100) * (crit_damage / 100 - 1)
        return tuple(scaling * crit_factor * (1 + (element + (
            self.stat_value(key, stats) if key else 0)) / 100) for key in SKILL_TYPES)

    def main_matches(self, echoes):
        choices = dict(self.main_choices)
        return sum(bool(echo.main and echo.main[0][0] in choices.get(echo.cost, ()))
                   for echo in echoes)

    def goals(self, normal, triggered):
        stats = triggered if self.targets_triggered else normal
        scaling, crit, crit_damage, energy, _ = self.panel(stats)
        attack = scaling
        if self.target_attack and self.scaling != "攻击":
            attack = self.panel_attribute("攻击", stats)
        core = tuple((name, value, target) for name, value, target in (
            ("暴击", min(100, crit), self.target_crit_rate),
            ("暴击伤害", crit_damage, self.target_crit_damage),
            (ENERGY, energy, self.target_energy),
            ("攻击", attack, self.target_attack),
        ) if target)
        return core + tuple((name, self.panel_attribute(name, stats), target)
                            for name, target in self.additional_targets if target)

    def target_gap(self, normal, triggered):
        gap = 0
        for name, value, target in self.goals(normal, triggered):
            # A crit-rate goal is in this loop only when the profile enables it.
            weight = 1.15 if name == "暴击" and self.target_crit_damage else 1
            gap += max(0, target - value) / ROLL_REFERENCE.get(name, 8) * weight
        return round(gap, 8)

    def score(self, normal, triggered, main_matches=0, recommended_bonus=False):
        if self.ranking == "推荐词条":
            effective = triggered if self.targets_triggered else normal
            score = 0
            ranks = self.priority_ranks or range(len(self.priorities))
            for name, rank in zip(self.priorities, ranks):
                value = effective[STAT_INDEX[name]]
                if name == "暴击":
                    crit = self.character.base_stats["暴击"] if self.character else self.crit_rate
                    value = min(value, max(0, 100 - crit - self.stat_value(name, ZERO)))
                if name == ENERGY and self.target_energy:
                    value = min(value, max(0, self.target_energy - self.total_energy(ZERO)))
                score += value / ROLL_REFERENCE.get(name, 8) * (.84 ** rank)
            return score + .40 * main_matches + float(recommended_bonus)
        off = self.scores(normal)
        on = self.scores(triggered)
        return sum(weight * ((1 - fraction) * a + fraction * b)
                   for weight, fraction, a, b in zip(self.weights, self.coverage, off, on)
                   ) / sum(self.weights)


@dataclass(frozen=True)
class Build:
    echoes: tuple
    first: object
    score: float
    normal: tuple
    triggered: tuple
    effects: tuple
    gap: float = 0
    main_matches: int = 0

    @property
    def rank(self):
        return -self.gap, self.score


@dataclass(frozen=True)
class SearchResult:
    builds: list
    complete: bool
    visited: int
    candidates: int
    sonata_types: int = 0
    set_shortages: tuple = ()
    missing_first: bool = False
    diagnostics: tuple = ()


def evaluate(profile, echoes, stats=None, *, first_options=None, main_matches=None):
    if stats is None:
        stats = ZERO
        for echo in echoes:
            stats = add_stats(stats, echo.stats())
    if first_options is None:
        neutral_first = next((echo for echo in echoes if canonical_echo_name(echo.name) not in FIRST_ECHO), None)
        first_options = [echo for echo in echoes if canonical_echo_name(echo.name) in FIRST_ECHO]
        if neutral_first:
            first_options.append(neutral_first)
    if main_matches is None:
        main_matches = profile.main_matches(echoes)
    recommended_bonus = any(len({canonical_echo_name(echo.name) for echo in echoes if echo.sonata == sonata}) >= 5
                            for sonata in profile.recommended_sets)
    builds = []
    for first in first_options:
        normal, triggered, active = set_effects(echoes, first)
        normal = add_stats(stats, normal)
        if profile.total_energy(normal) + 1e-9 < profile.minimum_energy:
            continue
        triggered = add_stats(normal, triggered)
        builds.append(Build(tuple(echoes), first,
                            profile.score(normal, triggered, main_matches, recommended_bonus),
                            normal, triggered, active, profile.target_gap(normal, triggered), main_matches))
    return max(builds, key=lambda build: build.rank, default=None)


def maximum_by_stat(vectors):
    return tuple(max(values) for values in zip(*vectors))


def explain_main_shortages(patterns, groups, species):
    messages = []
    for index, (layout, openings) in enumerate(patterns.items(), 1):
        missing = []
        for (cost, stat), count in Counter(layout).items():
            available = len({species[echo.id] for echo in groups.get((cost, stat), ())})
            if available < count:
                missing.append(f"{cost} COST {stat} 需 {count} 件，可用 {available} 件，还差 {count - available} 件")
        ordered_openings = sorted(openings)
        if all(first for first, _ in ordered_openings) and not any(
                species[echo.id][1] == first
                for first, slot in ordered_openings for echo in groups.get(slot, ())):
            choices = " / ".join(f"「{first}」的 {cost} COST {stat}"
                                 for first, (cost, stat) in ordered_openings)
            missing.append(f"首位需要 {choices}")
        if not missing:
            return ("可用声骸无法同时满足攻略主词条、首位、合鸣套装和 COST 要求。",)
        label = f"攻略方案 {index}" if len(patterns) > 1 else "攻略主词条不足"
        messages.append(label + " · " + "；".join(missing))
    return tuple(messages)


def explain_inventory_shortages(inventory, scoped, excluded, species, shortages, wanted_firsts):
    messages = []
    if wanted_firsts:
        names = " / ".join(wanted_firsts)
        matches = [echo for echo in inventory if canonical_echo_name(echo.name) in wanted_firsts]
        scoped_ids = {echo.id for echo in scoped}
        blocked = sorted({f"{echo.name}（{echo.owner}）" for echo in matches if echo.id in scoped_ids})
        if blocked:
            messages.append("推荐首位因穿戴状态不可用 · " + "、".join(blocked))
        elif matches:
            sonatas = "、".join(sorted({echo.sonata for echo in matches}))
            messages.append(f"推荐首位「{names}」在「{sonatas}」，不符合所选套装。")
        else:
            messages.append(f"推荐首位「{names}」未入库。")
    available_species = set(species.values())
    for sonata, _, _ in shortages:
        reserved = defaultdict(set)
        for echo in excluded:
            key = (echo.sonata, canonical_echo_name(echo.name))
            if echo.sonata == sonata and key not in available_species:
                reserved[echo.owner].add(key[1])
        if reserved:
            count = len(set().union(*reserved.values()))
            owners = "、".join(sorted(reserved))
            messages.append(f"「{sonata}」另有 {count} 种因穿戴状态不可用（{owners}）。")
    return tuple(messages)


def solve(profile, inventory, *, borrow=False, cost_limit=12, required_set="", set_requirements=(),
          first_echoes=(), main_layouts=(), limit=5,
          seconds=10, stopped=lambda: False):
    profile.validate()
    requirements = dict(set_requirements or (((required_set, 5),) if required_set else ()))
    set_slots = sum(requirements.values())
    if set_slots > 5 or any(count <= 0 for count in requirements.values()):
        raise ValueError("合鸣套装件数超出五件声骸")
    inventory = tuple(inventory)
    scoped = [echo for echo in inventory if set_slots < 5 or echo.sonata in requirements]
    echoes = [echo for echo in scoped if not echo.owner or echo.owner == profile.name
              or (borrow and echo.owner not in {UNKNOWN_OWNER, "装配中（待确认）"})]
    species = {echo.id: (echo.sonata, canonical_echo_name(echo.name)) for echo in echoes}
    counts = {name: len({echo_name for sonata, echo_name in species.values() if sonata == name})
              for name in requirements}
    sonata_types = next(iter(counts.values())) if len(counts) == 1 else 0
    shortages = tuple((name, counts[name], target)
                      for name, target in requirements.items() if counts[name] < target)
    patterns = defaultdict(set)
    for first, slots in main_layouts:
        if len(slots) != 5 or any(cost not in {1, 3, 4} or stat not in STAT_INDEX for cost, stat in slots):
            raise ValueError("攻略方案必须包含五个有效的主词条槽位")
        layout = tuple(sorted(slots, key=lambda slot: (-slot[0], slot[1])))
        patterns[layout].add((canonical_echo_name(first), slots[0]))
    groups = defaultdict(list)
    for echo in echoes:
        if patterns:
            if echo.main:
                groups[echo.cost, echo.main[0][0]].append(echo)
        else:
            groups[echo.cost, ""].append(echo)
    first_names = tuple(dict.fromkeys(map(canonical_echo_name, first_echoes)))
    first_ids = {echo.id for echo in echoes if species[echo.id][1] in first_names}
    missing_first = bool(first_names and not first_ids)
    if shortages or missing_first:
        excluded = [echo for echo in scoped if echo.id not in species]
        diagnostics = explain_inventory_shortages(
            inventory, scoped, excluded, species, shortages, first_names if missing_first else ()
        )
        return SearchResult([], True, 0, len(echoes), sonata_types, shortages, missing_first, diagnostics)
    if len(echoes) < 5:
        diagnostics = [f"可用声骸还差 {5 - len(echoes)} 件才能凑满五件。"]
        excluded = [echo for echo in scoped if echo.id not in species]
        if excluded:
            owners = "、".join(sorted({echo.owner for echo in excluded}))
            diagnostics.append(f"另有 {len(excluded)} 件因穿戴状态不可用（{owners}）。")
        if patterns:
            diagnostics.extend(explain_main_shortages(patterns, groups, species))
        return SearchResult([], True, 0, len(echoes), sonata_types, diagnostics=tuple(diagnostics))
    minimum_cost = sum(sorted(echo.cost for echo in echoes)[:5])
    if minimum_cost > cost_limit:
        diagnostics = (f"最便宜的五件也需要 COST {minimum_cost}，超过上限 {cost_limit}。",)
        return SearchResult([], True, 0, len(echoes), sonata_types, diagnostics=diagnostics)
    vectors = {echo.id: echo.stats() for echo in echoes}
    for group in groups.values():
        group.sort(key=lambda e: (e.id in first_ids, -profile.target_gap(vectors[e.id], vectors[e.id]),
                                  profile.score(vectors[e.id], vectors[e.id], profile.main_matches((e,)))),
                   reverse=True)
    if patterns:
        jobs = []
        for layout, openings in patterns.items():
            if any(not first for first, _ in openings):
                allowed_first_ids = first_ids if first_names else None
            else:
                allowed_first_ids = {
                    echo.id for first, slot in openings for echo in groups[slot]
                    if species[echo.id][1] == first and (not first_names or echo.id in first_ids)
                }
            jobs.append((layout, allowed_first_ids))
    else:
        jobs = [(layout, first_ids if first_names else None) for layout in
                combinations_with_replacement(((1, ""), (3, ""), (4, "")), 5)]
        jobs.sort(key=lambda job: sum(cost for cost, _ in job[0]), reverse=True)
    jobs = [(layout, allowed_first_ids) for layout, allowed_first_ids in jobs
            if sum(cost for cost, _ in layout) <= cost_limit
            and all(len(groups[slot]) >= count for slot, count in Counter(layout).items())]
    best = []
    visited = 0
    deadline = monotonic() + seconds
    bonus_bound = maximum_bonuses(echoes)
    maxima = {key: maximum_by_stat([vectors[e.id] for e in group])
              for key, group in groups.items() if group}

    class Interrupted(Exception):
        pass

    def find(layout, allowed_first_ids):
        nonlocal visited
        first_slots = {slot for slot in layout if allowed_first_ids is not None
                       and any(echo.id in allowed_first_ids for echo in groups[slot])}
        suffix = [ZERO] * 6
        for index in range(4, -1, -1):
            suffix[index] = add_stats(suffix[index + 1], maxima[layout[index]])

        def visit(depth, start, selected, totals, distinct, has_first):
            nonlocal visited
            visited += 1
            if visited % 128 == 1:
                if stopped() or monotonic() >= deadline:
                    raise Interrupted
                yield
            if not has_first and first_slots.isdisjoint(layout[depth:]):
                return
            needed = sum(max(0, target - sum(sonata == name for sonata, _ in distinct))
                         for name, target in requirements.items())
            if needed > 5 - depth:
                return
            optimistic = add_stats(add_stats(totals, suffix[depth]), bonus_bound)
            if profile.total_energy(optimistic) + 1e-9 < profile.minimum_energy:
                return
            if len(best) == limit:
                upper = (-profile.target_gap(optimistic, optimistic),
                         profile.score(optimistic, optimistic, 5, True))
                if upper < best[-1].rank:
                    return
            if depth == 5:
                first_options = (None if allowed_first_ids is None else
                                 [echo for echo in selected if echo.id in allowed_first_ids])
                build = evaluate(profile, selected, totals, first_options=first_options,
                                 main_matches=5 if patterns else None)
                if build:
                    best.append(build)
                    best.sort(key=lambda item: item.rank, reverse=True)
                    del best[limit:]
                return
            slot = layout[depth]
            group = groups[slot]
            for pos in range(start, len(group) - layout[depth:].count(slot) + 1):
                echo = group[pos]
                next_distinct = distinct | {species[echo.id]} if echo.sonata in requirements else distinct
                if set_slots == 5 and next_distinct == distinct:
                    continue
                next_start = pos + 1 if depth < 4 and layout[depth + 1] == slot else 0
                yield from visit(depth + 1, next_start, selected + (echo,),
                                 add_stats(totals, vectors[echo.id]), next_distinct,
                                 has_first or echo.id in allowed_first_ids)

        yield from visit(0, 0, (), ZERO, frozenset(), allowed_first_ids is None)

    unfinished = deque(find(layout, allowed_first_ids) for layout, allowed_first_ids in jobs)
    try:
        while unfinished:
            search = unfinished.popleft()
            if next(search, False) is None:
                unfinished.append(search)
    except Interrupted:
        return SearchResult(best, False, visited, len(echoes), sonata_types)
    diagnostics = explain_main_shortages(patterns, groups, species) if patterns and not best else ()
    return SearchResult(best, True, visited, len(echoes), sonata_types, diagnostics=diagnostics)
