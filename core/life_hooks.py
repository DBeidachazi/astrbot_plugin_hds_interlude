"""本地扩展：奇遇引擎（Life Hooks）。

宿主每天按概率给她「发牌」：一两件来自她线下世界的具体小事（闺蜜约她、妈妈让她跑腿、
楼下来了只猫……）。模型在追求连贯、不得编造的约束下总会回到最保守的写法，变数必须由宿主
注入；钩子作为**本区间里已经发生的观察事实**交给模型，她接不接、怎么接由她自己决定。

设计要点：
- 当天抽什么、几点揭晓，由 `(story_id, 日期)` 决定的伪随机数生成——同一天反复计算结果
  一致，所以抽签结果不用落库；落库的只有「哪些钩子已经交付、结果如何」。
- 钩子库在 `plugin_data/astrbot_plugin_hds_interlude/life_hooks.json`，按 mtime 热读取；
  文件缺失或损坏时使用本文件内置的默认库。
- 本模块是纯函数：日型（school / rest / holiday / break / exam）由调用方传入，
  不在这里依赖 real_calendar，便于在没有 chinese_calendar 的环境里测试。
"""

from __future__ import annotations

import hashlib
import json
import os
import random
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any, Optional
from zoneinfo import ZoneInfo

#: 日型分类。`off` 是 rest/holiday/break 的别名。
DAY_CATEGORIES = ('school', 'rest', 'holiday', 'break', 'exam')
_OFF = ('rest', 'holiday', 'break')

#: 交付记录最多保留的天数（冷却与每周预算只需要最近一段）。
HISTORY_DAYS = 30
#: 一个回合最多注入的钩子数。
MAX_PER_TURN = 2

DEFAULT_CONFIG: dict[str, Any] = {
    'enabled': True,
    # 牌库钩子与世界事件（合并模式）共享的每日交付上限；聊天种子另算 seed_daily_cap。
    'daily_total_cap': 3,
    'seed_daily_cap': 2,
    # 世界播种器开启时，把它的事件并进钩子通道（贴合人设的提示词、记录反应、可开主线）。
    'merge_seeder': True,
    # 每天的小波澜数量上限（实际在 1..daily_minor 之间抽）。
    'daily_minor': 2,
    # 每 7 天最多几个中等事件；有余额时每天按概率尝试一次。
    'weekly_medium': 1,
    'medium_chance': {'school': 0.12, 'off': 0.3},
    # 没写 hours 的钩子默认揭晓时段（本地小时，左闭右开）。
    'default_hours': {'school': [17, 22], 'off': [9, 21], 'exam': [19, 21]},
    'hooks': [
        {'id': 'friend-invite', 'weight': 3, 'tier': 'minor', 'days': ['off'], 'hours': [9, 19],
         'event': '苏棠发来消息，约她{slot}去{place}',
         'vars': {'slot': ['今天下午', '傍晚', '明天'], 'place': ['新开的甜品店', '商场里的书店', '江边公园', '音游机厅', '老城区小吃街']},
         'cast': ['苏棠'], 'cooldown_days': 2},
        {'id': 'errand', 'weight': 3, 'tier': 'minor', 'days': ['off', 'school'], 'hours': [10, 20],
         'event': '妈妈让她去楼下超市买{item}',
         'vars': {'item': ['酱油和鸡蛋', '一把小葱和豆腐', '煤球的猫粮', '明早的面包']},
         'cast': ['妈妈'], 'cooldown_days': 2},
        {'id': 'weather-turn', 'weight': 2, 'tier': 'minor', 'days': ['off', 'school'],
         'event': '{weather}',
         'vars': {'weather': ['窗外突然下起了雨，雨点打在窗台上', '傍晚的晚霞烧得特别好看，整片天都是橘粉色',
                              '起风了，楼下的桂花香一阵阵飘上来']},
         'cooldown_days': 3},
        {'id': 'stray-cat', 'weight': 1, 'tier': 'minor', 'days': ['off', 'school'], 'hours': [16, 20],
         'event': '楼下花坛边出现了一只脏兮兮的小橘猫，冲着人细声叫', 'arc': 'stray-cat', 'cooldown_days': 10},
        {'id': 'neighbor-kid', 'weight': 1, 'tier': 'minor', 'days': ['off'], 'hours': [14, 19],
         'event': '楼下的小宇来敲门，{ask}',
         'vars': {'ask': ['说想借她的彩笔', '拿着数学作业说有道题不会', '说自己的风筝挂树上了']},
         'cast': ['小宇'], 'cooldown_days': 4},
        {'id': 'dad-snack', 'weight': 1, 'tier': 'minor', 'days': ['off', 'school'], 'hours': [18, 21],
         'event': '爸爸出差回来了，拎回来{snack}', 'vars': {'snack': ['一袋当地特产小点心', '一包没见过的零食', '一盒鲜花饼']},
         'cast': ['爸爸'], 'cooldown_days': 7},
        {'id': 'deskmate-note', 'weight': 2, 'tier': 'minor', 'days': ['school'], 'hours': [12, 17],
         'event': '同桌周屿把一张写满物理解题步骤的草稿纸推到她桌上', 'cast': ['周屿'], 'cooldown_days': 4},
        {'id': 'milk-tea', 'weight': 2, 'tier': 'minor', 'days': ['school'], 'hours': [17, 18],
         'event': '放学路过奶茶店，陈姐冲她招手说今天上了新品', 'cast': ['陈姐'], 'cooldown_days': 3},
        {'id': 'guitar-club', 'weight': 1, 'tier': 'medium', 'days': ['school', 'off'], 'hours': [12, 20],
         'event': '吉他社群里贴出了校园文艺汇演的报名通知，江晚学姐还单独@了她', 'cast': ['江晚学姐'],
         'arc': 'guitar-f-chord', 'cooldown_days': 30},
        {'id': 'lost-and-found', 'weight': 1, 'tier': 'medium', 'days': ['off'], 'hours': [10, 18],
         'event': '她在路边长椅上捡到一本夹着旧车票和手写便签的笔记本', 'arc': 'lost-notebook', 'cooldown_days': 30},
    ],
}


# ---------------------------------------------------------------------------- 配置


def _config_path() -> Path:
    env = os.environ.get('HDSI_LIFE_HOOKS_CONFIG')
    if env:
        return Path(env)
    return Path(__file__).resolve().parents[3] / 'plugin_data' / 'astrbot_plugin_hds_interlude' / 'life_hooks.json'


_cache: dict[str, Any] = {'mtime': None, 'value': None}


def load_config() -> dict[str, Any]:
    """热读取钩子库；缺失或损坏时用默认库。顶层键浅合并。"""
    path = _config_path()
    try:
        mtime = path.stat().st_mtime
    except OSError:
        mtime = None
    if _cache['value'] is not None and _cache['mtime'] == mtime:
        return _cache['value']
    cfg = json.loads(json.dumps(DEFAULT_CONFIG))
    if mtime is not None:
        try:
            raw = json.loads(path.read_text(encoding='utf-8'))
            if isinstance(raw, dict):
                cfg.update({key: value for key, value in raw.items() if key in cfg})
        except Exception:  # noqa: BLE001 - 配置损坏时使用默认库
            pass
    _cache.update(mtime=mtime, value=cfg)
    return cfg


# ---------------------------------------------------------------------------- 抽签


def category_matches(days: Any, category: str) -> bool:
    wanted = days if isinstance(days, list) and days else ['off', 'school']
    for item in wanted:
        if item == category or (item == 'off' and category in _OFF):
            return True
    return False


def _hours(hook: dict[str, Any], category: str, cfg: dict[str, Any]) -> tuple[int, int]:
    hours = hook.get('hours')
    if not (isinstance(hours, list) and len(hours) == 2):
        defaults = cfg.get('default_hours') or DEFAULT_CONFIG['default_hours']
        key = 'school' if category == 'school' else 'exam' if category == 'exam' else 'off'
        hours = defaults.get(key) or [9, 21]
    start, end = int(hours[0]), int(hours[1])
    return max(0, min(23, start)), max(start + 1, min(24, end))


def _rng(*parts: Any) -> random.Random:
    seed = hashlib.sha256('|'.join(str(part) for part in parts).encode('utf-8')).hexdigest()
    return random.Random(int(seed[:16], 16))


def _render(template: str, variables: Any, rng: random.Random) -> str:
    text = str(template or '')
    if isinstance(variables, dict):
        for name in sorted(variables):
            options = variables[name]
            if isinstance(options, list) and options:
                text = text.replace('{%s}' % name, str(rng.choice(options)))
    return text


def _weighted_pick(rng: random.Random, pool: list[dict[str, Any]], count: int) -> list[dict[str, Any]]:
    chosen: list[dict[str, Any]] = []
    pool = list(pool)
    while pool and len(chosen) < count:
        weights = [max(0.0, float(hook.get('weight', 1) or 0)) for hook in pool]
        total = sum(weights)
        if total <= 0:
            break
        mark = rng.random() * total
        for index, weight in enumerate(weights):
            mark -= weight
            if mark < 0:
                chosen.append(pool.pop(index))
                break
        else:
            chosen.append(pool.pop())
    return chosen


def _on_cooldown(hook: dict[str, Any], day: date, history: dict[str, Any]) -> bool:
    cooldown = max(0, int(hook.get('cooldown_days', 1) or 0))
    for record in history.values():
        if not isinstance(record, dict) or record.get('hook') != hook.get('id'):
            continue
        try:
            last = date.fromisoformat(str(record.get('date')))
        except ValueError:
            continue
        if 0 <= (day - last).days < max(1, cooldown) and last != day:
            return True
    return False


def _medium_used(day: date, history: dict[str, Any]) -> int:
    used = 0
    for record in history.values():
        if not isinstance(record, dict) or record.get('tier') != 'medium':
            continue
        try:
            when = date.fromisoformat(str(record.get('date')))
        except ValueError:
            continue
        # 只数此前 6 天：当天已交付的不计，否则交付后当天计划会变（随机序列错位）。
        if 0 < (day - when).days < 7:
            used += 1
    return used


def daily_plan(story_id: str, day: date, category: str, tz: str,
               history: Optional[dict[str, Any]] = None, cfg: Optional[dict[str, Any]] = None) -> list[dict[str, Any]]:
    """这一天的钩子与揭晓时刻（确定性）。

    `history` 是已交付记录（`{instance_id: {hook, tier, date, ...}}`），用于冷却与每周中等事件预算。
    返回 `[{id, hook, tier, event, cast, arc, revealAt(UTC datetime)}]`，按揭晓时间排序。
    """
    cfg = cfg or load_config()
    history = history if isinstance(history, dict) else {}
    if not cfg.get('enabled', True) or category not in DAY_CATEGORIES:
        return []
    hooks = [hook for hook in cfg.get('hooks') or [] if isinstance(hook, dict) and hook.get('id')]
    eligible = [
        hook for hook in hooks
        if category_matches(hook.get('days'), category)
        and (not hook.get('months') or day.month in hook.get('months'))
        and not _on_cooldown(hook, day, history)
    ]
    rng = _rng(story_id, day.isoformat())
    minor_pool = [hook for hook in eligible if hook.get('tier', 'minor') != 'medium']
    medium_pool = [hook for hook in eligible if hook.get('tier') == 'medium']
    limit = max(0, int(cfg.get('daily_minor', 2) or 0))
    picked = _weighted_pick(rng, minor_pool, rng.randint(1, limit) if limit else 0)
    chance_cfg = cfg.get('medium_chance') or {}
    chance = float(chance_cfg.get('school' if category == 'school' else 'off', 0) or 0)
    medium_roll = rng.random()
    if (medium_pool and _medium_used(day, history) < int(cfg.get('weekly_medium', 1) or 0)
            and category != 'exam' and medium_roll < chance):
        picked += _weighted_pick(rng, medium_pool, 1)
    zone = ZoneInfo(tz or 'Asia/Shanghai')
    plan = []
    for hook in picked:
        start, end = _hours(hook, category, cfg)
        minute = rng.randint(start * 60, end * 60 - 1)
        local = datetime.combine(day, time(minute // 60, minute % 60), tzinfo=zone)
        plan.append({
            'id': '%s#%s' % (hook['id'], day.isoformat()),
            'hook': hook['id'],
            'tier': hook.get('tier', 'minor'),
            'event': _render(hook.get('event', ''), hook.get('vars'), rng),
            'cast': [str(name) for name in hook.get('cast') or []],
            'arc': hook.get('arc') or None,
            'revealAt': local.astimezone(ZoneInfo('UTC')),
        })
    plan.sort(key=lambda item: item['revealAt'])
    return plan


def due_hooks(story_id: str, now: datetime, category: str, tz: str,
              history: Optional[dict[str, Any]] = None, cfg: Optional[dict[str, Any]] = None) -> list[dict[str, Any]]:
    """已到揭晓时间、还没交付过的当天钩子（最多 MAX_PER_TURN 个）。"""
    history = history if isinstance(history, dict) else {}
    local_day = now.astimezone(ZoneInfo(tz or 'Asia/Shanghai')).date()
    plan = daily_plan(story_id, local_day, category, tz, history, cfg)
    return [item for item in plan if item['revealAt'] <= now and item['id'] not in history][:MAX_PER_TURN]


def prompt_hooks(hooks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """发给模型的形状。"""
    return [{'id': item['id'], 'event': item['event'],
             **({'cast': item['cast']} if item.get('cast') else {}),
             **({'guide': item['guide']} if item.get('guide') else {})}
            for item in hooks]


_OUTCOMES = ('taken', 'declined', 'postponed')


def record_outcomes(history: Any, delivered: list[dict[str, Any]], outcomes: Any,
                    now: datetime, tz: str) -> dict[str, Any]:
    """把本回合交付的钩子与模型回报的结果写进交付记录（返回新字典）。

    模型漏报时记为 `unreported`；回报了未交付的 id 一律忽略。超过 HISTORY_DAYS 的记录剪掉。
    """
    result = dict(history) if isinstance(history, dict) else {}
    reported: dict[str, dict[str, Any]] = {}
    for item in outcomes if isinstance(outcomes, list) else []:
        if isinstance(item, dict) and isinstance(item.get('id'), str):
            reported[item['id']] = item
    local_day = now.astimezone(ZoneInfo(tz or 'Asia/Shanghai')).date()
    for hook in delivered:
        answer = reported.get(hook['id']) or {}
        outcome = answer.get('outcome') if answer.get('outcome') in _OUTCOMES else 'unreported'
        result[hook['id']] = {
            'hook': hook['hook'], 'tier': hook['tier'], 'date': local_day.isoformat(),
            'event': hook['event'][:200], 'outcome': outcome,
            'note': str(answer.get('note') or '')[:200], 'arc': hook.get('arc'),
            'at': now.astimezone(ZoneInfo('UTC')).isoformat().replace('+00:00', 'Z'),
        }
    cutoff = local_day - timedelta(days=HISTORY_DAYS)
    for key in list(result):
        try:
            if date.fromisoformat(str(result[key].get('date'))) < cutoff:
                del result[key]
        except (ValueError, AttributeError):
            del result[key]
    return result
