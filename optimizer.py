from collections import deque
from dataclasses import dataclass
from itertools import combinations_with_replacement
import math
from time import monotonic

from echoes import STAT_INDEX, UNKNOWN_OWNER, ZERO, add_stats, canonical_echo_name
from rules import ENERGY, FIRST_ECHO, SKILL_TYPES, maximum_bonuses, set_effects


ROLL_REFERENCE = {"暴击": 8, "暴击伤害": 16, ENERGY: 10, "攻击": 45,
                  "防御": 50, "生命": 450, "治疗效果加成": 10}


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

    def validate(self):
        values = (self.base, self.extra_flat, self.extra_percent, self.crit_rate,
                  self.crit_damage, self.elemental_bonus, self.energy,
                  self.minimum_energy, self.target_crit_rate, self.target_crit_damage,
                  self.target_energy, *self.weights, *self.coverage)
        if (not self.name.strip() or any(not math.isfinite(v) or v < 0 for v in values)
                or self.base <= 0 or self.crit_damage < 100 or not any(self.weights)
                or len(self.weights) != len(SKILL_TYPES) or len(self.coverage) != len(SKILL_TYPES)
                or self.target_crit_rate > 100
                or (self.target_crit_damage and self.target_crit_damage < 100)
                or any(x > 1 for x in self.coverage)
                or self.scaling not in {"攻击", "生命", "防御"}
                or self.element + "伤害加成" not in STAT_INDEX
                or self.ranking not in {"推荐词条", "循环伤害"}
                or (self.ranking == "推荐词条" and not self.priorities)
                or len(set(self.priorities)) != len(self.priorities)
                or (self.priority_ranks and (len(self.priority_ranks) != len(self.priorities)
                    or any(not isinstance(rank, int) or rank < 0 for rank in self.priority_ranks)))
                or any(name not in STAT_INDEX for _, names in self.main_choices for name in names)
                or any(name not in STAT_INDEX for name in self.priorities)):
            raise ValueError("请检查面板、双暴目标、技能比例和推荐词条设置")

    def panel(self, stats):
        value = lambda name: stats[STAT_INDEX[name]]
        scaling = self.base * (1 + (self.extra_percent + value(self.scaling + "%")) / 100)
        scaling += self.extra_flat + value(self.scaling)
        return (scaling, self.crit_rate + value("暴击"), self.crit_damage + value("暴击伤害"),
                self.energy + value(ENERGY), self.elemental_bonus + value(self.element + "伤害加成"))

    def scores(self, stats):
        scaling, crit, crit_damage, _, element = self.panel(stats)
        crit_factor = 1 + min(1, crit / 100) * (crit_damage / 100 - 1)
        return tuple(scaling * crit_factor * (1 + (element + (
            stats[STAT_INDEX[key]] if key else 0)) / 100) for key in SKILL_TYPES)

    def main_matches(self, echoes):
        choices = dict(self.main_choices)
        return sum(bool(echo.main and echo.main[0][0] in choices.get(echo.cost, ()))
                   for echo in echoes)

    def goals(self, normal, triggered):
        _, crit, crit_damage, energy, _ = self.panel(triggered if self.targets_triggered else normal)
        return tuple((name, value, target) for name, value, target in (
            ("暴击", min(100, crit), self.target_crit_rate),
            ("暴击伤害", crit_damage, self.target_crit_damage),
            (ENERGY, energy, self.target_energy),
        ) if target)

    def target_gap(self, normal, triggered):
        return round(sum(max(0, target - value) / ROLL_REFERENCE[name]
                         for name, value, target in self.goals(normal, triggered)), 8)

    def score(self, normal, triggered, main_matches=0, recommended_bonus=False):
        if self.ranking == "推荐词条":
            effective = triggered if self.targets_triggered else normal
            score = 0
            ranks = self.priority_ranks or range(len(self.priorities))
            for name, rank in zip(self.priorities, ranks):
                value = effective[STAT_INDEX[name]]
                if name == "暴击":
                    value = min(value, max(0, 100 - self.crit_rate))
                if name == ENERGY and self.target_energy:
                    value = min(value, max(0, self.target_energy - self.energy))
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


def evaluate(profile, echoes, stats=None, *, first_options=None):
    if stats is None:
        stats = ZERO
        for echo in echoes:
            stats = add_stats(stats, echo.stats())
    if first_options is None:
        neutral_first = next((echo for echo in echoes if canonical_echo_name(echo.name) not in FIRST_ECHO), None)
        first_options = [echo for echo in echoes if canonical_echo_name(echo.name) in FIRST_ECHO]
        if neutral_first:
            first_options.append(neutral_first)
    main_matches = profile.main_matches(echoes)
    recommended_bonus = any(len({canonical_echo_name(echo.name) for echo in echoes if echo.sonata == sonata}) >= 5
                            for sonata in profile.recommended_sets)
    builds = []
    for first in first_options:
        normal, triggered, active = set_effects(echoes, first)
        normal = add_stats(stats, normal)
        if profile.energy + normal[STAT_INDEX[ENERGY]] + 1e-9 < profile.minimum_energy:
            continue
        triggered = add_stats(normal, triggered)
        builds.append(Build(tuple(echoes), first,
                            profile.score(normal, triggered, main_matches, recommended_bonus),
                            normal, triggered, active, profile.target_gap(normal, triggered), main_matches))
    return max(builds, key=lambda build: build.rank, default=None)


def maximum_by_stat(vectors):
    return tuple(max(values) for values in zip(*vectors))


def solve(profile, inventory, *, borrow=False, cost_limit=12, required_set="", set_requirements=(),
          first_echoes=(), limit=5,
          seconds=10, stopped=lambda: False):
    profile.validate()
    requirements = dict(set_requirements or (((required_set, 5),) if required_set else ()))
    set_slots = sum(requirements.values())
    if set_slots > 5 or any(count <= 0 for count in requirements.values()):
        raise ValueError("合鸣套装件数超出五件声骸")
    echoes = [echo for echo in inventory if (set_slots < 5 or echo.sonata in requirements)
              and (not echo.owner or echo.owner == profile.name
                   or (borrow and echo.owner not in {UNKNOWN_OWNER, "装配中（待确认）"}))]
    species = {echo.id: (echo.sonata, canonical_echo_name(echo.name)) for echo in echoes}
    counts = {name: len({echo_name for sonata, echo_name in species.values() if sonata == name})
              for name in requirements}
    sonata_types = next(iter(counts.values())) if len(counts) == 1 else 0
    shortages = tuple((name, counts[name], target)
                      for name, target in requirements.items() if counts[name] < target)
    first_names = frozenset(map(canonical_echo_name, first_echoes))
    first_ids = {echo.id for echo in echoes if species[echo.id][1] in first_names}
    missing_first = bool(first_names and not first_ids)
    if shortages or missing_first:
        return SearchResult([], True, 0, len(echoes), sonata_types, shortages, missing_first)
    first_costs = {echo.cost for echo in echoes if echo.id in first_ids}
    groups = {1: [], 3: [], 4: []}
    vectors = {}
    for echo in echoes:
        values = echo.stats()
        vectors[echo.id] = values
        groups[echo.cost].append(echo)
    for group in groups.values():
        group.sort(key=lambda e: (e.id in first_ids, -profile.target_gap(vectors[e.id], vectors[e.id]),
                                  profile.score(vectors[e.id], vectors[e.id], profile.main_matches((e,)))),
                   reverse=True)
    layouts = [layout for layout in combinations_with_replacement((1, 3, 4), 5)
               if sum(layout) <= cost_limit and all(layout.count(n) <= len(groups[n])
                                                    for n in groups)]
    layouts.sort(key=sum, reverse=True)
    best = []
    visited = 0
    deadline = monotonic() + seconds
    bonus_bound = maximum_bonuses(echoes)
    maxima = {key: maximum_by_stat([vectors[e.id] for e in group])
              for key, group in groups.items() if group}

    class Interrupted(Exception):
        pass

    def find(layout):
        nonlocal visited
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
            if not has_first and first_costs.isdisjoint(layout[depth:]):
                return
            needed = sum(max(0, target - sum(sonata == name for sonata, _ in distinct))
                         for name, target in requirements.items())
            if needed > 5 - depth:
                return
            optimistic = add_stats(add_stats(totals, suffix[depth]), bonus_bound)
            if profile.energy + optimistic[STAT_INDEX[ENERGY]] + 1e-9 < profile.minimum_energy:
                return
            if len(best) == limit:
                upper = (-profile.target_gap(optimistic, optimistic),
                         profile.score(optimistic, optimistic, 5, True))
                if upper < best[-1].rank:
                    return
            if depth == 5:
                first_options = [echo for echo in selected if echo.id in first_ids] if first_names else None
                build = evaluate(profile, selected, totals, first_options=first_options)
                if build:
                    best.append(build)
                    best.sort(key=lambda item: item.rank, reverse=True)
                    del best[limit:]
                return
            cost = layout[depth]
            group = groups[cost]
            for pos in range(start, len(group) - layout[depth:].count(cost) + 1):
                echo = group[pos]
                next_distinct = distinct | {species[echo.id]} if echo.sonata in requirements else distinct
                if set_slots == 5 and next_distinct == distinct:
                    continue
                next_start = pos + 1 if depth < 4 and layout[depth + 1] == cost else 0
                yield from visit(depth + 1, next_start, selected + (echo,),
                                 add_stats(totals, vectors[echo.id]), next_distinct,
                                 has_first or echo.id in first_ids)

        yield from visit(0, 0, (), ZERO, frozenset(), not first_names)

    unfinished = deque(find(layout) for layout in layouts)
    try:
        while unfinished:
            search = unfinished.popleft()
            if next(search, False) is None:
                unfinished.append(search)
    except Interrupted:
        return SearchResult(best, False, visited, len(echoes), sonata_types)
    return SearchResult(best, True, visited, len(echoes), sonata_types)
