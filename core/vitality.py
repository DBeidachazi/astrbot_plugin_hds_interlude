"""本地扩展：生活活力（防停滞、睡眠合并推进、钩子与长线剧情的编排）。

主叙事请求只多出 `interval.lifeStagnation` / `interval.lifeHooks` / `interval.activeArcs` /
`interval.pastArcs` 几个字段；落库时回收模型的 `hookOutcomes` / `arcProgress`。所有状态放在
story state 的 `extensions.vitality`（`{"hooks": {...交付记录}, "arcs": {...主线状态}}`）。

这里的每个入口都吞掉异常并退回「什么都不加」：活力机制坏了，主叙事照常进行。
"""

from __future__ import annotations

import re
from datetime import date, datetime, time, timedelta
from typing import Any, Optional
from zoneinfo import ZoneInfo

from . import expression, life_hooks, life_seeds, story_arcs

#: 这些阶段会注入钩子（自主生活回合）；回复回合不被打断。
HOOK_PHASES = ('advance', 'conversation-follow-up')
#: 停滞阈值（小时）：非上课日 / 上课日放学后。
STAGNATION_HOURS = {'off': 3.0, 'school': 2.0}
#: 停滞判定最多回看多少段剧本。
STAGNATION_LOOKBACK = 24

_ASLEEP = re.compile(r'熟睡|沉睡|深睡|酣睡|入睡|睡着|安眠|睡眠|梦乡|黑甜乡|睡觉|回笼觉|安稳觉|准备睡')
_AWAKE = re.compile(r'醒来|醒了|起床|爬起|下床|睁开眼')

_PLACE_KEYS = (
    ('bedroom', re.compile(r'卧室|床上|被窝|床边|书桌')),
    ('living', re.compile(r'客厅|沙发')),
    ('kitchen', re.compile(r'厨房|餐厅|餐桌')),
    ('bath', re.compile(r'卫生间|洗手间|浴室|洗手台')),
    ('school', re.compile(r'学校|教室|校园|操场|食堂')),
)


# ---------------------------------------------------------------------------- 基础


def day_category(status: str) -> str:
    """real_calendar 的日型 status → 钩子/主线用的分类。"""
    if status in ('school', 'makeup_school', 'saturday_school'):
        return 'school'
    if status in ('gaokao', 'gaokao_prep'):
        return 'exam'
    if status == 'legal_holiday':
        return 'holiday'
    if status in ('winter_break', 'summer_break', 'graduation_summer'):
        return 'break'
    return 'rest'


def calendar_category(day: date) -> str:
    """当天分类；日历不可用时按周末/工作日粗分。"""
    try:
        from . import real_calendar
        cfg = real_calendar.load_config()
        if real_calendar.enabled(cfg):
            return day_category(real_calendar.classify_day(day, cfg)['status'])
    except Exception:  # noqa: BLE001
        pass
    return 'rest' if day.weekday() >= 5 else 'school'


def sleep_block_end(day: date) -> Optional[time]:
    """当天作息里 `sleep` 块的结束时刻（如上课日 06:00、休息日 09:00）。"""
    try:
        from . import real_calendar
        cfg = real_calendar.load_config()
        if real_calendar.enabled(cfg):
            for block in real_calendar.blocks_for(real_calendar.classify_day(day, cfg)):
                if block.get('id') == 'sleep':
                    hour, minute = (int(x) for x in str(block['end']).split(':'))
                    return time(hour, minute)
    except Exception:  # noqa: BLE001
        pass
    return time(9, 0) if day.weekday() >= 5 else time(6, 0)


def _get(record: Any, *keys: str) -> Any:
    if not isinstance(record, dict):
        return None
    for key in keys:
        if record.get(key) is not None:
            return record[key]
    return None


def _parse(value: Any) -> Optional[datetime]:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=ZoneInfo('UTC'))
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=ZoneInfo('UTC'))


def handoffs(entries: Any) -> list[dict[str, Any]]:
    """从剧本条目里取出 `[{at, place, activity}]`（时间升序），只看 script 条目。"""
    result = []
    for entry in entries if isinstance(entries, list) else []:
        if _get(entry, 'kind') != 'script':
            continue
        metadata = _get(entry, 'metadata')
        if isinstance(metadata, str):
            try:
                import json
                metadata = json.loads(metadata)
            except ValueError:
                metadata = None
        handoff = _get(metadata, 'life_handoff', 'lifeHandoff')
        at = _parse(_get(entry, 'occurredAt', 'occurred_at'))
        if not isinstance(handoff, dict) or at is None:
            continue
        result.append({
            'at': at,
            'place': str(_get(_get(handoff, 'place'), 'value') or ''),
            'activity': str(_get(_get(handoff, 'activity'), 'value') or ''),
        })
    result.sort(key=lambda item: item['at'])
    return result


def is_asleep(activity: str) -> bool:
    return bool(activity) and bool(_ASLEEP.search(activity)) and not _AWAKE.search(activity)


def place_key(place: str) -> str:
    for key, pattern in _PLACE_KEYS:
        if pattern.search(place):
            return key
    return ''.join(place.split())[:6]


def _latest_activity(items: list[dict[str, Any]]) -> str:
    for item in reversed(items):
        if item['activity']:
            return item['activity']
    return ''


# ---------------------------------------------------------------------------- 防停滞


def life_stagnation(entries: Any, now: datetime, tz: str,
                    category: Optional[str] = None) -> Optional[dict[str, Any]]:
    """醒着、在同一类地点停留超过阈值时返回 `{place, sinceLocal, hours}`，否则 None。"""
    zone = ZoneInfo(tz or 'Asia/Shanghai')
    local = now.astimezone(zone)
    category = category or calendar_category(local.date())
    if category == 'exam':
        return None
    if category == 'school' and local.hour < 17:
        return None  # 上课时间待在学校是正常的
    end = sleep_block_end(local.date())
    if end is not None and local.time() < end:
        return None
    items = handoffs(entries)[-STAGNATION_LOOKBACK:]
    if not items or is_asleep(_latest_activity(items)):
        return None
    latest_place = next((item['place'] for item in reversed(items) if item['place']), '')
    if not latest_place:
        return None
    key = place_key(latest_place)
    since = None
    for item in reversed(items):
        if not item['place']:
            continue
        if place_key(item['place']) != key or is_asleep(item['activity']):
            break
        since = item['at']
    if since is None:
        return None
    hours = (now - since).total_seconds() / 3600
    threshold = STAGNATION_HOURS['school' if category == 'school' else 'off']
    if hours < threshold:
        return None
    return {'place': latest_place, 'sinceLocal': since.astimezone(zone).strftime('%H:%M'),
            'hours': round(hours, 1)}


# ---------------------------------------------------------------------------- 睡眠合并推进


def sleep_resume_at(entries: Any, now: datetime, tz: str) -> Optional[datetime]:
    """她已经睡着时，下一次自动推进直接排到睡眠块结束（UTC）；否则 None。

    午睡（白天睡着）不合并，交给常规节奏。
    """
    items = handoffs(entries)
    if not items or not is_asleep(_latest_activity(items)):
        return None
    zone = ZoneInfo(tz or 'Asia/Shanghai')
    local = now.astimezone(zone)
    today_end = sleep_block_end(local.date())
    if today_end is not None and local.time() < today_end:
        resume = datetime.combine(local.date(), today_end, tzinfo=zone)
    elif local.hour >= 20:
        tomorrow = local.date() + timedelta(days=1)
        resume = datetime.combine(tomorrow, sleep_block_end(tomorrow) or time(7, 0), tzinfo=zone)
    else:
        return None
    resume = resume.astimezone(ZoneInfo('UTC'))
    return resume if resume > now else None


# ---------------------------------------------------------------------------- 夜间免打扰

#: 夜里仍会叫醒她的群消息：明显的紧急求助（@ / 引用她由调用方另行判断）。
_URGENT = re.compile(r'紧急|急事|救命|出事了?|快醒醒|醒醒|快来人|报警|急急急|十万火急|SOS', re.IGNORECASE)
#: 起床后的第一次自主回合最多带多少条夜间群消息原文。
OVERNIGHT_MESSAGE_LIMIT = 15


def is_urgent(text: str) -> bool:
    return bool(text) and bool(_URGENT.search(text))


def night_over(now: datetime, tz: str) -> bool:
    """按钟点判断夜晚是否已经结束（过了当天睡眠块结束时刻、且还没到 20 点）。"""
    local = now.astimezone(ZoneInfo(tz or 'Asia/Shanghai'))
    end = sleep_block_end(local.date()) or time(7, 0)
    return end <= local.time() and local.hour < 20


def quiet_inbox_add(vitality_state: Any, count: int, now: datetime) -> dict[str, Any]:
    """记一笔「睡着时没打扰她的群消息」（返回新的 vitality 状态）。"""
    vitality = dict(vitality_state) if isinstance(vitality_state, dict) else {}
    inbox = dict(vitality.get('sleep_inbox') or {})
    stamp = now.astimezone(ZoneInfo('UTC')).isoformat().replace('+00:00', 'Z')
    inbox['since'] = inbox.get('since') or stamp
    inbox['last'] = stamp
    inbox['count'] = int(inbox.get('count') or 0) + max(0, int(count))
    vitality['sleep_inbox'] = inbox
    return vitality


def _speaker(entry: Any) -> str:
    metadata = _get(entry, 'metadata')
    if isinstance(metadata, str):
        try:
            import json
            metadata = json.loads(metadata)
        except ValueError:
            metadata = None
    return str(_get(metadata, 'senderName', 'sender_name') or _get(metadata, 'senderId', 'sender_id') or '群友')


def overnight_context(vitality_state: Any, entries: Any, now: datetime, tz: str) -> Optional[dict[str, Any]]:
    """夜晚结束后第一次自主回合要看到的 `overnightMessages`；没有积压时 None。"""
    inbox = _get(vitality_state, 'sleep_inbox')
    if not isinstance(inbox, dict) or not int(inbox.get('count') or 0) or not night_over(now, tz):
        return None
    since = _parse(inbox.get('since'))
    zone = ZoneInfo(tz or 'Asia/Shanghai')
    recent = []
    for entry in entries if isinstance(entries, list) else []:
        at = _parse(_get(entry, 'occurredAt', 'occurred_at'))
        if _get(entry, 'kind') != 'group-message' or at is None or (since and at < since):
            continue
        recent.append({'atLocal': at.astimezone(zone).strftime('%H:%M'), 'speaker': _speaker(entry),
                       'content': str(_get(entry, 'content') or '')[:200]})
    return {
        'count': int(inbox['count']),
        'sinceLocal': since.astimezone(zone).strftime('%H:%M') if since else '',
        'recent': recent[-OVERNIGHT_MESSAGE_LIMIT:],
    }


# ---------------------------------------------------------------------------- 请求 / 落库


def _vitality_state(story_state: Any) -> dict[str, Any]:
    extensions = _get(story_state, 'extensions')
    vitality = _get(extensions, 'vitality')
    return vitality if isinstance(vitality, dict) else {}


def _story_tz(story: Any) -> str:
    setting = _get(story, 'setting')
    return str(_get(setting, 'timezone') or 'Asia/Shanghai')


def _story_id(story: Any) -> str:
    return str(_get(story, 'id') or '')


def seeder_merge_enabled() -> bool:
    """世界播种器事件是否并进钩子通道（生活钩子开着且 merge_seeder=True）。"""
    try:
        cfg = life_hooks.load_config()
        return bool(cfg.get('enabled', True) and cfg.get('merge_seeder', True))
    except Exception:  # noqa: BLE001
        return False


def turn_hooks(story: Any, story_state: Any, phase: str, now: datetime,
               world_rows: Any = None) -> list[dict[str, Any]]:
    """本回合要注入的钩子（decide 与 persist 用同一个函数，结果一致）。

    三个来源按优先级合并：聊天种子（用户种下的事，另有每日上限）→ 世界事件（合并模式）→
    牌库钩子；世界事件与牌库共享 `daily_total_cap`，每回合最多 `life_hooks.MAX_PER_TURN` 个。
    """
    if phase not in HOOK_PHASES:
        return []
    cfg = life_hooks.load_config()
    if not cfg.get('enabled', True):
        return []
    tz = _story_tz(story)
    local_day = now.astimezone(ZoneInfo(tz)).date()
    vitality = _vitality_state(story_state)
    history = vitality.get('hooks') if isinstance(vitality.get('hooks'), dict) else {}
    hooks: list[dict[str, Any]] = []
    if life_seeds.today_count(history, local_day, ('seed',)) < int(cfg.get('seed_daily_cap', 2) or 0):
        hooks += life_seeds.due_seed_hooks(vitality.get('seeds'), now, 1)
    room = min(life_hooks.MAX_PER_TURN - len(hooks),
               int(cfg.get('daily_total_cap', 3) or 0)
               - life_seeds.today_count(history, local_day, ('minor', 'medium', 'world')))
    if room > 0 and world_rows and seeder_merge_enabled():
        found = life_seeds.world_hooks(world_rows, now, history, room)
        hooks += found
        room -= len(found)
    if room > 0:
        hooks += life_hooks.due_hooks(
            _story_id(story), now, calendar_category(local_day), tz, history,
        )[:room]
    return hooks


def request_context(story: Any, story_state: Any, phase: str, now: datetime,
                    entries: Any, world_rows: Any = None, her_messages: Any = None) -> dict[str, Any]:
    """主叙事请求里 `interval` 要加的字段。"""
    try:
        tz = _story_tz(story)
        local_day = now.astimezone(ZoneInfo(tz)).date()
        category = calendar_category(local_day)
        result: dict[str, Any] = {}
        stagnation = life_stagnation(entries, now, tz, category)
        if stagnation:
            result['lifeStagnation'] = stagnation
        hooks = turn_hooks(story, story_state, phase, now, world_rows)
        if hooks:
            result['lifeHooks'] = life_hooks.prompt_hooks(hooks)
        result.update(story_arcs.context(_vitality_state(story_state).get('arcs'), local_day))
        own = list(her_messages) if isinstance(her_messages, list) else expression.her_messages(entries)
        budget = expression.prompt_budget(own, story_state)
        if budget:
            result['expressionBudget'] = budget
        if phase in HOOK_PHASES:
            overnight = overnight_context(_vitality_state(story_state), entries, now, tz)
            if overnight:
                result['overnightMessages'] = overnight
        else:
            echo = life_seeds.echoes(_vitality_state(story_state).get('seeds'), now)
            if echo:
                result['seedEchoes'] = echo
        return result
    except Exception:  # noqa: BLE001 - 活力机制绝不影响主叙事
        return {}


def seeder_context(story: Any, story_state: Any, now: datetime) -> dict[str, Any]:
    """合并模式下给世界播种器的额外输入：日型、聊天种子、主线、最近的钩子。"""
    try:
        tz = _story_tz(story)
        local_day = now.astimezone(ZoneInfo(tz)).date()
        vitality = _vitality_state(story_state)
        arcs_state = vitality.get('arcs') if isinstance(vitality.get('arcs'), dict) else {}
        arcs_ctx = story_arcs.context(arcs_state, local_day)
        available = [
            {'id': arc['id'], 'title': arc.get('title', arc['id']), 'opening': arc['stages'][0]}
            for arc in story_arcs.load_config().get('arcs') or []
            if isinstance(arc, dict) and arc.get('id') and arc.get('stages') and arc['id'] not in arcs_state
        ]
        history = vitality.get('hooks') if isinstance(vitality.get('hooks'), dict) else {}
        recent = sorted(
            (record for record in history.values() if isinstance(record, dict)),
            key=lambda record: str(record.get('at') or ''), reverse=True,
        )[:10]
        return {
            'dayType': calendar_category(local_day),
            'chatSeeds': life_seeds.open_seed_digest(vitality.get('seeds')),
            'activeArcs': [
                {'id': arc['id'], 'title': arc['title'], 'stage': arc['stage'], 'nextBeat': arc['nextBeat']}
                for arc in arcs_ctx.get('activeArcs') or []
            ],
            'availableArcs': available,
            'recentLifeHooks': [str(record.get('event') or '') for record in recent if record.get('event')],
        }
    except Exception:  # noqa: BLE001
        return {}


def record_turn(story: Any, story_state: Any, phase: str, now: datetime,
                raw_decision: Any, script: str) -> tuple[Optional[dict[str, Any]], list[str]]:
    """落库时回收钩子结果与主线推进。返回 `(新的 vitality 状态或 None, 日志行)`。"""
    state, logs, _world = record_turn_full(story, story_state, phase, now, raw_decision, script)
    return state, logs


def record_turn_full(story: Any, story_state: Any, phase: str, now: datetime, raw_decision: Any,
                     script: str, world_rows: Any = None) -> tuple[Optional[dict[str, Any]], list[str], list[Any]]:
    """`record_turn` 的完整版：另外返回本回合交付掉的世界事件行 id（调用方负责标记 injected）。"""
    try:
        tz = _story_tz(story)
        local_day = now.astimezone(ZoneInfo(tz)).date()
        category = calendar_category(local_day)
        vitality = dict(_vitality_state(story_state))
        before = repr(vitality)
        logs: list[str] = []

        delivered = turn_hooks(story, story_state, phase, now, world_rows)
        if delivered:
            outcomes = _get(raw_decision, 'hookOutcomes', 'hook_outcomes')
            vitality['hooks'] = life_hooks.record_outcomes(vitality.get('hooks'), delivered, outcomes, now, tz)
            for hook in delivered:
                record = vitality['hooks'][hook['id']]
                logs.append('生活钩子 %s 结果=%s 事件=%s' % (hook['id'], record['outcome'], hook['event']))
            by_id = {item['id']: item for item in outcomes if isinstance(item, dict) and isinstance(item.get('id'), str)} \
                if isinstance(outcomes, list) else {}
            if any(hook.get('seed_id') for hook in delivered):
                vitality['seeds'] = life_seeds.settle_seeds(vitality.get('seeds'), delivered, by_id, now)

        # 聊天种子：这一回合聊天里种下、她之后可能真去做的事。
        cast = life_seeds.cast_names(_get(_get(story, 'setting'), 'supporting_cast', 'supportingCast'))
        drafts = life_seeds.normalize_seeds(_get(raw_decision, 'lifeSeeds', 'life_seeds'), cast, now)
        if drafts:
            vitality['seeds'], added = life_seeds.add_seeds(vitality.get('seeds'), drafts, now, tz)
            for seed in added:
                logs.append('聊天种子 %s 来自=%s 立场=%s 内容=%s' % (seed['kind'], seed['from'], seed['stance'], seed['what']))

        arcs = vitality.get('arcs') if isinstance(vitality.get('arcs'), dict) else {}
        for hook in delivered:
            record = vitality['hooks'][hook['id']]
            if hook.get('arc') and record['outcome'] in ('taken', 'postponed', 'unreported'):
                arcs, started = story_arcs.start_arc(arcs, hook['arc'], local_day, 'hook:%s' % hook['id'])
                if started:
                    logs.append('长线剧情开启 %s（来自钩子 %s）' % (hook['arc'], hook['id']))
        if phase in HOOK_PHASES:
            spontaneous = story_arcs.maybe_spontaneous(_story_id(story), arcs, local_day, category)
            if spontaneous:
                arcs, started = story_arcs.start_arc(arcs, spontaneous, local_day, 'spontaneous')
                if started:
                    logs.append('长线剧情开启 %s（自发）' % spontaneous)
        arcs, changes = story_arcs.apply_progress(
            arcs, _get(raw_decision, 'arcProgress', 'arc_progress'), script, local_day,
        )
        for change in changes:
            logs.append('长线剧情 %s %s %s' % (change['id'], change['change'], change.get('stage') or change.get('title') or ''))
        if arcs:
            vitality['arcs'] = arcs
        inbox = vitality.get('sleep_inbox')
        if phase in HOOK_PHASES and isinstance(inbox, dict) and night_over(now, tz):
            vitality.pop('sleep_inbox', None)  # 已在本回合的 overnightMessages 里交给她
            logs.append('夜间群消息已在起床后交付 条数=%s' % inbox.get('count'))
        world_ids = [hook['row_id'] for hook in delivered if hook.get('row_id') is not None]
        return (vitality if repr(vitality) != before else None), logs, world_ids
    except Exception as error:  # noqa: BLE001
        return None, ['活力状态回收失败：%s' % error], []
