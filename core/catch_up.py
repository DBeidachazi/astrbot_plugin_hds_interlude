"""本地扩展：晨间回信（2026-10-06）。

用户反馈：「小满晚上睡觉的时候，私聊的消息早上不会回」。复盘（10-06）：好小狗凌晨 04:01 的 3 条私聊
各触发一次主叙事，她睡着、不回——这没错；但夜里没有任何东西记下「醒来要回」，醒来后的自主回合里
回信又被当成「主动联系」，要过 proactiveContact / 意愿 / 设备隐私窗口 / 每日上限几道门，提示词里也没有
「你刚醒、昨晚有人找你」。数据库里从没出现过「醒来回昨晚私聊」这件事。

做法：每次后台扫描看一眼她在剧本里是不是睡着（按剧本交接里的当前活动，不按钟点——假期赖床到九点也算
睡着）。从「睡着」变成「醒着」的那一刻，给每个还在等她回复的私聊排一条到期回访（`follow-up-commitment`，
到期时以那个人的私聊回合处理，回复直接进那个对话，不走主动联系的门），错峰几分钟，最信任的人排前面。

本模块是纯函数；状态在 `story.state.extensions.catch_up`：`{asleep_since, last_wake_at}`。
"""

from __future__ import annotations

import random
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Optional
from zoneinfo import ZoneInfo

from . import vitality

#: 只回这么久以内的来信（更早的当历史，不硬回）。
MAX_AGE = timedelta(hours=24)
#: 入睡前不久收到、还没回的也算（睡前没看到 / 看到了没回）。
BEFORE_SLEEP = timedelta(hours=1)
#: 第一条回访在醒来后几分钟；之后每人再隔几分钟；再加一点抖动。
FIRST_DELAY = timedelta(minutes=3)
STEP = timedelta(minutes=6)
JITTER_MINUTES = 3
TTL = timedelta(hours=6)
#: 睡着至少这么久才算「一觉醒来」：剧本里偶尔一条误判（或打个盹）不触发回访。
MIN_SLEEP = timedelta(minutes=90)


def _get(record: Any, *keys: str) -> Any:
    if not isinstance(record, dict):
        return None
    for key in keys:
        if record.get(key) is not None:
            return record[key]
    return None


def _parse(value: Any) -> Optional[datetime]:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    try:
        moment = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
    except (TypeError, ValueError):
        return None
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


def _iso(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).isoformat(timespec='milliseconds').replace('+00:00', 'Z')


def asleep_in_story(entries: Any) -> Optional[bool]:
    """剧本里她最近的活动是不是在睡觉；没有可用的交接信息时 None（不下结论）。"""
    items = vitality.handoffs(entries)
    if not items:
        return None
    activity = vitality._latest_activity(items)  # noqa: SLF001 - 同包内的工具函数
    if not activity:
        return None
    return vitality.is_asleep(activity)


def _state(story_state: Any) -> dict[str, Any]:
    extensions = _get(story_state, 'extensions') or {}
    value = extensions.get('catch_up') if isinstance(extensions, dict) else None
    return dict(value) if isinstance(value, dict) else {}


def waiting_participants(participants: Any, since: datetime, now: datetime) -> list[dict[str, Any]]:
    """还在等她回复、且来信落在 [since, now] 里（不超过 24 小时）的私聊参与者。"""
    floor = max(since, now - MAX_AGE)
    result = []
    for participant in participants or []:
        if not isinstance(participant, dict) or participant.get('status', 'active') != 'active':
            continue
        state = _get(participant, 'state') or {}
        if isinstance(state, str):
            try:
                import json
                state = json.loads(state)
            except ValueError:
                state = {}
        if not (_get(state, 'pendingReplyCount', 'pending_reply_count')
                or _get(state, 'unreadMessageCount', 'unread_message_count')):
            continue
        last_user = _parse(_get(state, 'lastUserMessageAt', 'last_user_message_at'))
        last_character = _parse(_get(state, 'lastCharacterMessageAt', 'last_character_message_at'))
        if last_user is None or last_user < floor or last_user > now:
            continue
        if last_character is not None and last_character >= last_user:
            continue  # 已经回过了
        result.append(participant)
    return result


def _display_name(participant: dict[str, Any]) -> str:
    name = str(_get(participant, 'displayName', 'display_name') or '').strip()
    return name or '对方'


def _summary(participant: dict[str, Any], count: int, first_at: Optional[datetime], tz: str) -> str:
    when = ''
    if first_at is not None:
        local = first_at.astimezone(ZoneInfo(tz or 'Asia/Shanghai'))
        when = '（%s）' % ('凌晨 %d:%02d' % (local.hour, local.minute) if local.hour < 6 else '%d:%02d' % (local.hour, local.minute))
    return ('她刚睡醒拿起手机，看到 %s 在她睡着时%s发来的%s消息，当时睡着了没看到。'
            '先回一下：可以顺口说一句昨晚睡着了 / 刚醒，然后接着回应对方说的内容。'
            % (_display_name(participant), when, (' %d 条' % count) if count else ''))


def plan(story_state: Any, asleep: Optional[bool], now: datetime, participants: Any,
         trust_of: Callable[[dict[str, Any]], str] = lambda _p: '', tz: str = 'Asia/Shanghai',
         rng: Optional[Callable[[], float]] = None) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """一次扫描的决定：返回 `(新的 extensions.catch_up, 要排的回访意图草稿)`。

    - 睡着：记下 `asleep_since`（第一次看到睡着的时刻）；
    - 醒着且之前记过 `asleep_since`：刚醒 → 给等待中的私聊排回访，清掉标记；
    - 说不准（None）：什么都不改。
    """
    state = _state(story_state)
    if asleep is None:
        return state, []
    if asleep:
        if not state.get('asleep_since'):
            state['asleep_since'] = _iso(now)
        return state, []
    since = _parse(state.get('asleep_since'))
    if since is None:
        return state, []
    state.pop('asleep_since', None)
    if now - since < MIN_SLEEP:
        return state, []  # 打盹 / 误判：清掉标记，不算醒来
    state['last_wake_at'] = _iso(now)
    waiting = waiting_participants(participants, since - BEFORE_SLEEP, now)
    waiting.sort(key=lambda item: (
        0 if trust_of(item) == 'high' else 1,
        _parse(_get(_get(item, 'state') or {}, 'lastUserMessageAt', 'last_user_message_at')) or now,
    ))
    draw = rng if callable(rng) else random.random
    drafts = []
    for index, participant in enumerate(waiting):
        participant_state = _get(participant, 'state') or {}
        count = int(_get(participant_state, 'pendingReplyCount', 'pending_reply_count')
                    or _get(participant_state, 'unreadMessageCount', 'unread_message_count') or 0)
        first_at = _parse(_get(participant_state, 'lastUserMessageAt', 'last_user_message_at'))
        not_before = now + FIRST_DELAY + STEP * index + timedelta(minutes=float(draw()) * JITTER_MINUTES)
        drafts.append({
            'participantId': participant.get('id'),
            'type': 'follow-up-commitment',
            'summary': _summary(participant, count, first_at, tz),
            'notBefore': _iso(not_before),
            'payload': {
                'kind': 'reply', 'sourceEntryIds': [], 'expiresAt': _iso(now + TTL),
                'requiresVisibleOutcome': True, 'userInitiated': True, 'originDeliveryEventId': None,
                'morningCatchUp': True, 'asleepSince': _iso(since), 'messageCount': count,
            },
        })
    return state, drafts


def is_catch_up_intent(intent: Any) -> bool:
    payload = _get(intent, 'payload') or {}
    if isinstance(payload, str):
        try:
            import json
            payload = json.loads(payload)
        except ValueError:
            payload = {}
    return bool(_get(payload, 'morningCatchUp')) or str(_get(payload, 'manual') or '').startswith('morning-catch-up')


def answered_since(participant: Any, created_at: Any) -> bool:
    """这个人在回访创建之后已经收到她的消息（回过了）。

    2026-10-06 实测：晨间回访那一回合她已经回了好小狗，但模型没有交 followUpResolutions，
    回访被推后 40 分钟——放着不管，10:25 会再回一遍。回过了就直接结清。
    """
    state = _get(participant, 'state') or {}
    if isinstance(state, str):
        try:
            import json
            state = json.loads(state)
        except ValueError:
            state = {}
    replied = _parse(_get(state, 'lastCharacterMessageAt', 'last_character_message_at'))
    created = _parse(created_at)
    return replied is not None and created is not None and replied >= created
