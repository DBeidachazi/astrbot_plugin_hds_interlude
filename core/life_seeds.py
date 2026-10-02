"""本地扩展：聊天种子（Life Seeds）与世界事件的钩子化。

用户反馈（2026-10-02）：「我邀请小满出去玩她并不会理我，好像无法参与到她真正的生活中」。
根因之一是聊天内容从不沉淀进她之后的生活：模型几乎从不输出 `intents`，群聊原文又不进
自主推进回合。这里补一条更直接的通道：

1. 聊天回合里，模型用 `lifeSeeds` 记下「这次聊天种下、她之后可能真去做的事」——
   推荐（歌 / 番 / 游戏 / 吃的 / 地方）、线上一起做的事、邀约（含她婉拒的线下见面）。
2. 宿主把种子存进 `state.extensions.vitality.seeds`，到时间后在自主回合里当作生活钩子
   交给她（她真的去听、去看、去吃；婉拒的见面变成和线下朋友一起去，或一个线上替代）。
3. 她做完以后，之后的聊天回合会收到 `seedEchoes`，可以自然地说「你推荐的那家我去吃了」。

安全边界不变：线上认识的人提议线下见面，种子立场一律记为 declined（宿主兜底强制），
它只能以「她和线下朋友去 / 线上替代 / 想起这件事」的方式回响。

世界播种器的事件在合并模式下也走钩子通道（`world_hook`），共享每日预算、记录她的反应，
并可开启长线剧情。

本模块是纯函数，不碰数据库与日历。
"""

from __future__ import annotations

import hashlib
import re
from datetime import date, datetime, timedelta
from typing import Any, Optional
from zoneinfo import ZoneInfo

SEED_KINDS = ('try', 'online-together', 'invite')
SEED_STANCES = ('keen', 'maybe', 'declined')
#: 每回合最多收几颗种子；最多同时挂着几颗未完成的种子。
MAX_SEEDS_PER_TURN = 3
MAX_OPEN_SEEDS = 20
#: 一颗种子最多交付几次（推迟 / 漏报会再给一次机会）。
MAX_DELIVERIES = 2
#: 没给时间的种子，在创建后多久（小时，按 id 确定性取值）交给她。
SEED_DELAY_HOURS = (2, 18)
#: 推迟 / 漏报后多久再给。
RETRY_HOURS = {'postponed': 12, 'unreported': 6}
#: 回响：做完的种子在之后多久内可被她在聊天里提起，最多几条。
ECHO_HOURS = 72
ECHO_LIMIT = 3
#: 种子与钩子记录保留天数。
HISTORY_DAYS = 30

_SPACE = re.compile(r'\s+')


def _utc_iso(value: datetime) -> str:
    return value.astimezone(ZoneInfo('UTC')).isoformat().replace('+00:00', 'Z')


def _parse(value: Any) -> Optional[datetime]:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=ZoneInfo('UTC'))
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace('Z', '+00:00'))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=ZoneInfo('UTC'))


def _clean(text: Any, limit: int) -> str:
    return _SPACE.sub(' ', str(text or '')).strip()[:limit]


def _key(what: str, source: str) -> str:
    return re.sub(r'[\s，。！？、,.!?~～]', '', (what + '|' + source).lower())


def cast_names(supporting_cast: Any) -> list[str]:
    """从配角设定文本里取名字（「· 苏棠（闺蜜…）」→ 苏棠）。"""
    names = []
    for line in str(supporting_cast or '').splitlines():
        match = re.match(r'^\s*[·•\-*]?\s*([^（(：:，,\s]{1,12})', line)
        if match and match.group(1) not in names and not match.group(1).startswith('这些'):
            names.append(match.group(1))
    return names


# ---------------------------------------------------------------------------- 收种


def normalize_seeds(raw: Any, cast: list[str], now: datetime) -> list[dict[str, Any]]:
    """模型给的 `lifeSeeds` → 规范草稿。线上的人提议线下见面一律记为 declined。"""
    seeds = []
    for item in raw if isinstance(raw, list) else []:
        if not isinstance(item, dict):
            continue
        kind = item.get('kind') if item.get('kind') in SEED_KINDS else None
        what = _clean(item.get('what'), 60)
        if not kind or len(what) < 2:
            continue
        source = _clean(item.get('from'), 30) or '群友'
        stance = item.get('stance') if item.get('stance') in SEED_STANCES else 'maybe'
        offline_friend = any(name and name in source for name in cast)
        if kind == 'invite' and not offline_friend:
            stance = 'declined'  # 安全边界：线上认识的人约线下见面，永远只是一声回响
        when = _parse(item.get('when'))
        seeds.append({
            'kind': kind, 'what': what, 'from': source, 'stance': stance,
            'when': _utc_iso(when) if when and when > now else None,
        })
        if len(seeds) >= MAX_SEEDS_PER_TURN:
            break
    return seeds


def _seed_delay(seed_id: str) -> timedelta:
    low, high = SEED_DELAY_HOURS
    digest = int(hashlib.sha256(seed_id.encode('utf-8')).hexdigest()[:8], 16)
    return timedelta(minutes=low * 60 + digest % ((high - low) * 60))


def add_seeds(store: Any, drafts: list[dict[str, Any]], now: datetime, tz: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """把新种子并进种子簿（同一件事 7 天内只记一次）。返回 `(新种子簿, 新加的种子)`。"""
    result = {key: dict(value) for key, value in (store or {}).items() if isinstance(value, dict)} \
        if isinstance(store, dict) else {}
    local_day = now.astimezone(ZoneInfo(tz or 'Asia/Shanghai')).date()
    recent_keys = {
        _key(seed.get('what', ''), seed.get('from', ''))
        for seed in result.values()
        if (_parse(seed.get('createdAt')) or now) > now - timedelta(days=7)
    }
    added = []
    for draft in drafts:
        key = _key(draft['what'], draft['from'])
        if key in recent_keys:
            continue
        recent_keys.add(key)
        seed_id = '%s-%s' % (local_day.isoformat(), hashlib.sha256(key.encode('utf-8')).hexdigest()[:6])
        status = 'open'
        not_before = _parse(draft.get('when')) or (now + _seed_delay(seed_id))
        if draft['kind'] == 'online-together' and draft['stance'] == 'declined':
            status = 'dropped'  # 她明确不想一起玩的线上活动不再回响
        seed = {**draft, 'id': seed_id, 'status': status, 'createdAt': _utc_iso(now),
                'notBefore': _utc_iso(not_before), 'deliveries': 0}
        result[seed_id] = seed
        added.append(seed)
    # 未完成的种子太多时，丢最早的
    open_seeds = sorted((s for s in result.values() if s.get('status') == 'open'), key=lambda s: s.get('createdAt') or '')
    for seed in open_seeds[:max(0, len(open_seeds) - MAX_OPEN_SEEDS)]:
        result[seed['id']]['status'] = 'dropped'
    cutoff = now - timedelta(days=HISTORY_DAYS)
    for key in list(result):
        if (_parse(result[key].get('createdAt')) or now) < cutoff:
            del result[key]
    return result, added


# ---------------------------------------------------------------------------- 交付


def _seed_event(seed: dict[str, Any]) -> tuple[str, str]:
    """种子 → (钩子事件文本, 给作者的处理提示)。"""
    what, source, kind, stance = seed['what'], seed['from'], seed['kind'], seed['stance']
    if kind == 'invite' and stance == 'declined':
        return ('她又想起%s约她「%s」的事' % (source, what),
                '她不和线上认识的人线下见面；这份心意可以变成和线下朋友一起去、拍张照片留着分享，'
                '或是她想到一个线上也能一起做的替代——也可以只是想想')
    if kind == 'online-together':
        return ('和%s说好的「%s」，时间差不多到了' % (source, what),
                '她可以真的上线去做；如果对方是名单里的私聊参与者，可以用 crossConversationActions 打个招呼')
    if kind == 'invite':
        return ('和%s约好的「%s」' % (source, what), '按她当下的安排与心情决定是否成行')
    return ('她想起%s推荐的「%s」，正好有空可以试试' % (source, what),
            '真去听 / 看 / 吃 / 玩一下，留下具体的感受；也可以觉得一般或先放着')


def due_seed_hooks(store: Any, now: datetime, limit: int = 1) -> list[dict[str, Any]]:
    """到时间、还开着的种子 → 钩子（按 notBefore 先后，最多 `limit` 个）。"""
    seeds = [
        seed for seed in (store or {}).values()
        if isinstance(seed, dict) and seed.get('status') == 'open'
        and (_parse(seed.get('notBefore')) or now) <= now
    ] if isinstance(store, dict) else []
    seeds.sort(key=lambda seed: seed.get('notBefore') or '')
    hooks = []
    for seed in seeds[:max(0, limit)]:
        event, guide = _seed_event(seed)
        hooks.append({
            'id': 'seed:%s:%d' % (seed['id'], int(seed.get('deliveries') or 0) + 1),
            'hook': 'chat-seed', 'tier': 'seed', 'event': event, 'guide': guide,
            'cast': [], 'arc': None, 'seed_id': seed['id'],
        })
    return hooks


def settle_seeds(store: Any, delivered: list[dict[str, Any]], outcomes: dict[str, dict[str, Any]],
                 now: datetime) -> dict[str, Any]:
    """按本回合钩子结果更新种子：taken / declined 结束，postponed / 漏报改期，超次数丢弃。"""
    result = {key: dict(value) for key, value in (store or {}).items() if isinstance(value, dict)} \
        if isinstance(store, dict) else {}
    for hook in delivered:
        seed = result.get(hook.get('seed_id'))
        if not seed:
            continue
        answer = outcomes.get(hook['id']) or {}
        outcome = answer.get('outcome') if answer.get('outcome') in ('taken', 'declined', 'postponed') else 'unreported'
        seed['deliveries'] = int(seed.get('deliveries') or 0) + 1
        if outcome in ('taken', 'declined'):
            seed.update(status='done', outcome=outcome, note=str(answer.get('note') or '')[:120], doneAt=_utc_iso(now))
        elif seed['deliveries'] >= MAX_DELIVERIES:
            seed.update(status='dropped', outcome=outcome)
        else:
            seed['notBefore'] = _utc_iso(now + timedelta(hours=RETRY_HOURS[outcome]))
    return result


def echoes(store: Any, now: datetime) -> list[dict[str, Any]]:
    """最近做过的种子，聊天里可以自然提起。"""
    items = []
    for seed in (store or {}).values() if isinstance(store, dict) else []:
        done_at = _parse(seed.get('doneAt')) if isinstance(seed, dict) else None
        if not done_at or seed.get('outcome') != 'taken' or now - done_at > timedelta(hours=ECHO_HOURS):
            continue
        items.append({'what': seed['what'], 'from': seed['from'], 'kind': seed['kind'],
                      **({'note': seed['note']} if seed.get('note') else {}),
                      'doneAt': seed['doneAt']})
    items.sort(key=lambda item: item['doneAt'], reverse=True)
    return items[:ECHO_LIMIT]


def open_seed_digest(store: Any, limit: int = 8) -> list[dict[str, Any]]:
    """给世界播种器看的未完成种子摘要。"""
    seeds = [s for s in (store or {}).values() if isinstance(s, dict) and s.get('status') == 'open'] \
        if isinstance(store, dict) else []
    seeds.sort(key=lambda s: s.get('createdAt') or '', reverse=True)
    return [{'what': s['what'], 'from': s['from'], 'kind': s['kind'], 'stance': s['stance']} for s in seeds[:limit]]


# ---------------------------------------------------------------------------- 世界事件 → 钩子


def world_hooks(rows: Any, now: datetime, history: Any, limit: int) -> list[dict[str, Any]]:
    """到点的世界事件行（interlude_seeded_event）→ 钩子。已交付过的不再给。"""
    history = history if isinstance(history, dict) else {}
    due = []
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict) or row.get('status') != 'scheduled':
            continue
        occurs = _parse(row.get('occursAt') or row.get('occurs_at'))
        expires = _parse(row.get('expiresAt') or row.get('expires_at'))
        if occurs is None or occurs > now or (expires is not None and expires <= now):
            continue
        hook_id = 'world:%s' % row.get('id')
        if hook_id in history:
            continue
        due.append((occurs, row, hook_id))
    due.sort(key=lambda item: item[0])
    hooks = []
    for _occurs, row, hook_id in due[:max(0, limit)]:
        extras = row.get('sourcePayload') or row.get('source_payload') or {}
        extras = extras if isinstance(extras, dict) else {}
        hook = {
            'id': hook_id, 'hook': 'world-event', 'tier': 'world-%s' % (row.get('importance') or 'low'),
            'event': str(row.get('summary') or ''),
            'cast': [str(name) for name in (row.get('subjects') or []) if isinstance(name, str)],
            'arc': extras.get('arc') or None, 'row_id': row.get('id'),
        }
        if extras.get('response'):
            hook['guide'] = str(extras['response'])[:120]
        hooks.append(hook)
    return hooks


def today_count(history: Any, day: date, tiers: tuple[str, ...]) -> int:
    """某天已交付的钩子数（按 tier 前缀过滤）。"""
    count = 0
    for record in (history or {}).values() if isinstance(history, dict) else []:
        if not isinstance(record, dict) or record.get('date') != day.isoformat():
            continue
        tier = str(record.get('tier') or '')
        if any(tier == t or tier.startswith(t + '-') for t in tiers):
            count += 1
    return count
