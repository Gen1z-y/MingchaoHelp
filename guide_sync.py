"""Discover new simplified-Chinese guides and keep them available offline."""

from collections import Counter
from decimal import Decimal
from html import unescape
import json
import math
from pathlib import Path
import re
from tempfile import NamedTemporaryFile
from urllib.parse import parse_qs, urlencode, urlsplit
from urllib.request import Request, urlopen

from echoes import PERCENT_STATS, STAT_INDEX
from profiles import OFFICIAL_GUIDES, parse_guides, parse_skill_bonuses
from rules import ADDITIONAL_TARGETS


GUIDE_DOMAINS = (
    "https://guide-server-1.aki-game.com",
    "https://guide-server.aki-game.com",
    "https://guide-server.aki-game.net",
    "https://guide-server-1.aki-game.net",
)
ELEMENTS_BY_ID = dict(zip("123456", ("冷凝", "热熔", "导电", "气动", "衍射", "湮灭")))
TARGET_KEYS = {"暴击": "crit_rate", "暴击伤害": "crit_damage", "共鸣效率": "energy", "攻击": "attack"}
BASE_STATS = {"攻击", "生命", "防御", "暴击", "暴击伤害"}
WEAPON_TYPES = {"长刃", "迅刀", "佩枪", "臂铠", "音感仪"}
STAT_ALIASES = {
    **{name: name for name in STAT_INDEX},
    "暴击率": "暴击", "暴伤": "暴击伤害", "生命值": "生命", "生命值%": "生命%",
    "攻击力": "攻击", "攻击力%": "攻击%", "防御力": "防御", "防御力%": "防御%",
    "普攻伤害": "普攻伤害加成", "重击伤害": "重击伤害加成",
    "共鸣技能伤害": "共鸣技能伤害加成", "共鸣解放伤害": "共鸣解放伤害加成",
}
STAT_RE = re.compile("|".join(re.escape(name) for name in sorted(STAT_ALIASES, key=len, reverse=True)))


def valid_id(value):
    return bool(re.fullmatch(r"[0-9]+", str(value)))


def role_id_of(guide):
    return parse_qs(urlsplit(guide.url).query).get("role_id", [""])[0]


def chinese_text(records, key):
    return next((r[key].strip() for r in records or () if r.get("language") == "zh-Hans"
                 and isinstance(r.get(key), str) and r[key].strip()), "")


def fetch_guide_data(path, expected_type, **params):
    query = "?" + urlencode(params) if params else ""
    for domain in GUIDE_DOMAINS:
        request = Request(domain + path + query, headers={
            "x-language": "zh-Hans", "Referer": "https://mcguide.kurogames.com/",
            "User-Agent": "Mozilla/5.0",
        })
        try:
            with urlopen(request, timeout=5) as response:
                body = json.load(response)
            if body.get("code") == 200 and isinstance(body.get("data"), expected_type):
                return body["data"]
        except (OSError, ValueError, AttributeError):
            continue
    raise OSError("暂时无法读取库洛攻略站")


def read_cached_guides(directory):
    guides = {}
    for path in directory.glob("*.json"):
        if not valid_id(path.stem):
            continue
        try:
            item = json.loads(path.read_text(encoding="utf-8"))
            name = item["name"]
            guide = parse_guides({name: item["guide"]})[name]
            if (name and role_id_of(guide) == path.stem
                    and guide.main_layouts(guide.sonata_choices[0])):
                guides[name] = guide
        except (OSError, ValueError, KeyError, TypeError, IndexError, AttributeError):
            continue
    return guides


def parse_echo_plan(plan):
    if not plan:
        return None
    first = plan.get("echoProps") or {}
    first_name = chinese_text(first.get("texts"), "name")
    sets = {}
    for effect in plan.get("echoSetEffects") or ():
        name = chinese_text(effect.get("texts"), "name")
        count = effect.get("echoSet")
        if name and count in (2, 3, 5):
            sets[name] = max(sets.get(name, 0), count)
    slots = []
    for row in plan.get("echoAttributes") or ():
        cost = row.get("cost")
        stat = chinese_text((row.get("attribute") or {}).get("texts"), "name")
        stat = STAT_ALIASES.get(stat, stat)
        if cost not in (1, 3, 4) or stat not in STAT_INDEX:
            return None
        slots.append((cost, stat))
    if (not first_name or not sets or sum(sets.values()) > 5 or len(slots) != 5
            or sum(cost for cost, _ in slots) > 12 or first.get("cost") != slots[0][0]):
        return None
    return first_name, tuple(sets.items()), tuple(slots)


def parse_targets(detail):
    targets = {}
    supported = TARGET_KEYS.keys() | set(ADDITIONAL_TARGETS.values())
    for item in (detail.get("roleAttribute") or {}).get("items") or ():
        raw_name = chinese_text(item.get("texts"), "name")
        stat = STAT_ALIASES.get(raw_name, raw_name)
        raw = str(item.get("recommendAmount", "")).strip()
        match = re.fullmatch(r"(\d+(?:\.\d+)?)(%?)", raw)
        operation = item.get("operation")
        if (stat not in supported or not match or operation not in (4, 6)
                or bool(match[2]) != (stat in PERCENT_STATS)):
            continue
        amount = Decimal(match[1])
        if operation == 4:
            amount += Decimal(".1" if match[2] else "1")
        if (not 0 < amount or (stat == "暴击" and amount > 100)
                or (stat == "暴击伤害" and amount < 100)):
            continue
        value = float(amount)
        if math.isfinite(value):
            targets[stat] = max(targets.get(stat, 0), value)
    return targets


def parse_priorities(detail):
    description = chinese_text(detail.get("echoTexts"), "recommendDescription")
    text = unescape(re.sub(r"<[^>]*>", " ", description))
    seen = set()
    priorities = []
    for group in re.split(r"[>＞≥→；;。\n]", text):
        names = []
        for match in STAT_RE.finditer(group):
            name = STAT_ALIASES[match[0]]
            if name not in seen:
                seen.add(name)
                names.append(name)
        if names and ("=" in group or "＝" in group):
            priorities.append(" = ".join(names))
        else:
            priorities.extend(names)
    return priorities


def convert_guide(avatar, detail, strategy_id):
    role_id = str(avatar["roleGbId"])
    if (str((detail.get("role") or {}).get("roleGbId")) != role_id
            or str(detail.get("id")) != strategy_id
            or not chinese_text(detail.get("baseTexts"), "introductionName")):
        return None
    element = ELEMENTS_BY_ID.get(str((avatar.get("element") or {}).get("gbId")))
    name = chinese_text(avatar.get("texts"), "name")
    if not element or not name:
        return None
    if name == "漂泊者":
        name += "·" + element
    echo = detail.get("echo") or {}
    presets = tuple(dict.fromkeys(plan for key in ("main", "spare")
                                  if (plan := parse_echo_plan(echo.get(key)))))
    if not presets:
        return None
    mains = {}
    for _, _, slots in presets:
        for cost, stat in slots:
            mains.setdefault(cost, {}).setdefault(stat, None)
    primary_stats = Counter(stat for cost, stat in presets[0][2] if cost == 1)
    scaling = next((stat for stat in ("生命", "防御") if primary_stats[stat + "%"] >= 2), "攻击")
    targets = parse_targets(detail)
    items = (detail.get("weapon") or {}).get("items") or ()
    weapon_type = next((kind for item in items if (kind := chinese_text(
        (item.get("weaponType") or {}).get("texts"), "name")) in WEAPON_TYPES), "")
    return name, {
        "element": element, "scaling": scaling, "priorities": parse_priorities(detail),
        "mains": [(cost, list(stats)) for cost, stats in mains.items()],
        "sonatas": list(dict.fromkeys(sonata for _, sets, _ in presets for sonata, _ in sets)),
        "echo_presets": presets, "catalog_order": int(avatar.get("sequence") or 0),
        "weapon_type": weapon_type,
        "url": f"https://mcguide.kurogames.com/zh-Hans/?role_id={role_id}&strategy_id={strategy_id}",
        **{TARGET_KEYS[stat]: value for stat, value in targets.items() if stat in TARGET_KEYS},
        "additional_targets": {stat: value for stat, value in targets.items() if stat not in TARGET_KEYS},
    }


def read_character_panel(role_id):
    panel = {}
    for api in ("https://api.encore.moe/zh-Hans", "https://api-v2.encore.moe/api/zh-Hans"):
        request = Request(f"{api}/character/{role_id}", headers={"User-Agent": "Mozilla/5.0"})
        try:
            with urlopen(request, timeout=4) as response:
                data = json.load(response)
            weapon_type = data.get("WeaponTypeName")
            if str(data.get("Id")) != role_id or weapon_type not in WEAPON_TYPES:
                continue
            stats = {row["Name"]: float(str(row["GrowthValues"][-1]["value"]).rstrip("%"))
                     for row in data.get("Properties", ()) if row["Name"] in BASE_STATS
                     and row["GrowthValues"] and row["GrowthValues"][-1]["level"] == 90}
            if stats.keys() == BASE_STATS and all(math.isfinite(value) and value > 0 for value in stats.values()):
                panel = {"weapon_type": weapon_type, "base_stats": stats}
                bonuses = parse_skill_bonuses(data.get("SkillTree"))
                if bonuses is not None:
                    return {**panel, "skill_bonuses": bonuses}
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            continue
    return panel


def save_guide(directory, role_id, name, guide):
    directory.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with NamedTemporaryFile(dir=directory, suffix=".tmp", encoding="utf-8", mode="w", delete=False) as output:
            temporary = Path(output.name)
            json.dump({"name": name, "guide": guide}, output, ensure_ascii=False, indent=2)
        temporary.replace(directory / f"{role_id}.json")
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def sync_guides(directory, fetch=fetch_guide_data, character_panel=read_character_panel):
    cached = read_cached_guides(directory)
    known = OFFICIAL_GUIDES | cached
    known_ids = {role_id_of(guide) for guide in known.values()}
    avatars = fetch("/role/avatar/list", list)
    pending = not avatars
    additions = {}
    for name, guide in cached.items():
        if name in OFFICIAL_GUIDES or guide.skill_bonuses is not None:
            continue
        role_id = role_id_of(guide)
        try:
            panel = character_panel(role_id)
            if "skill_bonuses" not in panel:
                pending = True
                continue
            item = json.loads((directory / f"{role_id}.json").read_text(encoding="utf-8"))
            if item["name"] != name:
                continue
            item["guide"].update(panel)
            updated = parse_guides({name: item["guide"]})[name]
            save_guide(directory, role_id, name, item["guide"])
            cached[name] = updated
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            pending = True

    for avatar in avatars:
        role_id = str(avatar.get("roleGbId"))
        if not valid_id(role_id) or role_id in known_ids:
            continue
        try:
            options = fetch("/introduction/list", list, roleGbId=role_id)
            for option in options:
                strategy_id = str(option.get("id"))
                if not valid_id(strategy_id) or not chinese_text(option.get("texts"), "introductionName"):
                    continue
                try:
                    detail = fetch("/introduction/info", dict, roleGbId=role_id, id=strategy_id)
                    result = convert_guide(avatar, detail, strategy_id)
                except (OSError, ValueError, KeyError, TypeError, AttributeError):
                    continue
                if not result:
                    continue
                name, guide = result
                if name in known or name in additions:
                    name += "·" + guide["element"]
                if name in known or name in additions:
                    continue
                guide.update(character_panel(role_id))
                pending |= "skill_bonuses" not in guide
                save_guide(directory, role_id, name, guide)
                additions.update(parse_guides({name: guide}))
                known_ids.add(role_id)
                break
            else:
                pending = True
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            pending = True
    return cached | additions, pending
