"""本地扩展：高信任对象与「私聊里答应的群发言」（2026-10-05）。

用户反馈：「希望小满只是对我的话更相信一点，执行力更高一点，但对别人的态度保持当前的就可以」。
复盘（entry 5610–5641）：私聊里请她去群里夸一句，她连着拒绝三次；而且即使答应了，
私聊回合本身也发不了群（`crossConversationActions` 只能投给私聊参与者），只能靠下一次
群回合从共享时间线里「想起来」——群冷清时可能很久都没有群回合，热闹时承诺又会滚出上下文。

这里做两件事：
1. **信任档**：`qq_access.user_accounts[].trust = "high"` 的人，私聊回合的 payload 带
   `interval.trust`，提示词里有对应的行为规则（先信他、合理的小请求嘟囔两句最后照办）。
   只对这个人生效，其他人不变。
2. **群发言承诺**：高信任对象的私聊回合里，她真的答应了「去群里说…」时，模型返回
   `groupPromises:[{groupId, gist, quote}]`；宿主核对 quote 确实出现在本回合剧本里、
   groupId 在白名单内，记进剧本状态。该群下一次群回合（或 10 分钟后自己触发一次）在批次开头
   注入「你答应过…」，那一回合不受意愿门、冷却和「对别人说就不插嘴」拦截；她在群里发出来后
   承诺完成，两次都没发则作废。说什么、怎么说仍由她在群回合里自己写。

本模块是纯函数，状态放在 `story.state.extensions.trust`。
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from typing import Any, Optional

PROMISE_TTL = timedelta(hours=6)
PROMISE_TRIGGER_MS = 10 * 60 * 1000
MAX_ATTEMPTS = 2
MAX_PENDING = 3
MAX_GIST = 120


def _get(record: Any, *keys: str) -> Any:
    if not isinstance(record, dict):
        return None
    for key in keys:
        if record.get(key) is not None:
            return record[key]
    return None


def _norm(value: Any) -> str:
    return str(value or '').strip()


def _iso(moment: datetime) -> str:
    return moment.isoformat().replace('+00:00', 'Z')


def _parse(value: Any) -> Optional[datetime]:
    try:
        return datetime.fromisoformat(str(value).replace('Z', '+00:00'))
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------- 信任档


def trust_level(access_config: Any, participant: Any) -> str:
    """这个私聊参与者的信任档：'high' 或 ''。按 QQ 号对 `user_accounts` 里的 `trust` 字段。"""
    user_id = _norm(_get(participant, 'userId', 'user_id'))
    if not user_id:
        return ''
    for account in _get(access_config, 'userAccounts', 'user_accounts') or []:
        if not isinstance(account, dict) or account.get('enabled') is False:
            continue
        if _norm(account.get('qq')) == user_id:
            return 'high' if _norm(account.get('trust')).lower() == 'high' else ''
    return ''


def known_groups(access_config: Any) -> list[dict[str, str]]:
    """她所在、启用中的群：[{groupId, label}]。"""
    groups = []
    for rule in _get(access_config, 'groupChats', 'group_chats') or []:
        if not isinstance(rule, dict) or rule.get('enabled') is False:
            continue
        group_id = _norm(_get(rule, 'groupId', 'group_id'))
        if group_id:
            groups.append({'groupId': group_id, 'label': _norm(rule.get('label')) or group_id})
    return groups


def prompt_context(access_config: Any, participant: Any, phase: str) -> Optional[dict[str, Any]]:
    """私聊回合 `interval.trust`；不是高信任对象（或不是聊天回合）时 None。"""
    if phase not in ('user-message', 'conversation-follow-up') or trust_level(access_config, participant) != 'high':
        return None
    return {'level': 'high', 'knownGroups': known_groups(access_config)}


# ---------------------------------------------------------------- 群发言承诺


def _state(story_state: Any) -> dict[str, Any]:
    extensions = _get(story_state, 'extensions') or {}
    value = extensions.get('trust') if isinstance(extensions, dict) else None
    return dict(value) if isinstance(value, dict) else {}


def pending_promises(story_state: Any, now: datetime, group_id: Optional[str] = None) -> list[dict[str, Any]]:
    """未过期、未完成的承诺（可按群过滤），按时间先后。"""
    result = []
    for item in _state(story_state).get('group_promises') or []:
        if not isinstance(item, dict):
            continue
        expires = _parse(item.get('expiresAt'))
        if expires is not None and expires <= now:
            continue
        if group_id is not None and _norm(item.get('groupId')) != _norm(group_id):
            continue
        result.append(item)
    return result


def normalize_promises(raw: Any, groups: list[dict[str, str]], script: str, from_name: str,
                       participant_id: str, now: datetime) -> list[dict[str, Any]]:
    """模型给的 `groupPromises` → 承诺记录。quote 必须出现在本回合剧本里（她真的答应了），
    groupId 必须是她在的群；每回合最多一条。"""
    if not isinstance(raw, list):
        return []
    allowed = {item['groupId']: item['label'] for item in groups}
    text = str(script or '')
    for item in raw:
        if not isinstance(item, dict):
            continue
        group_id = _norm(_get(item, 'groupId', 'group_id'))
        gist = _norm(item.get('gist'))[:MAX_GIST]
        quote = _norm(item.get('quote'))
        if group_id not in allowed and len(allowed) == 1 and not group_id:
            group_id = next(iter(allowed))  # 只在一个群时允许省略
        if group_id not in allowed or not gist or not quote or quote not in text:
            continue
        return [{
            'id': uuid.uuid4().hex[:12], 'groupId': group_id, 'groupLabel': allowed[group_id],
            'gist': gist, 'from': from_name or '对方', 'participantId': participant_id,
            'createdAt': _iso(now), 'expiresAt': _iso(now + PROMISE_TTL), 'attempts': 0,
        }]
    return []


def add_promises(story_state: Any, promises: list[dict[str, Any]], now: datetime) -> dict[str, Any]:
    """返回新的 `extensions.trust`（同一群的旧承诺被新的取代，总数封顶）。"""
    state = _state(story_state)
    new_groups = {item['groupId'] for item in promises}
    kept = [item for item in pending_promises(story_state, now) if item.get('groupId') not in new_groups]
    state['group_promises'] = (kept + list(promises))[-MAX_PENDING:]
    return state


def settle(story_state: Any, group_id: str, posted: bool, now: datetime) -> tuple[dict[str, Any], list[str]]:
    """群回合结束：发出来了 → 完成；没发 → 尝试次数 +1，满 MAX_ATTEMPTS 作废。"""
    state = _state(story_state)
    logs: list[str] = []
    kept = []
    for item in pending_promises(story_state, now):
        if _norm(item.get('groupId')) != _norm(group_id):
            kept.append(item)
            continue
        if posted:
            logs.append('群发言承诺已兑现 群=%s 内容=%s 来自=%s' % (group_id, item.get('gist'), item.get('from')))
            continue
        attempts = int(item.get('attempts') or 0) + 1
        if attempts >= MAX_ATTEMPTS:
            logs.append('群发言承诺作废（%d 次群回合都没有发） 群=%s 内容=%s' % (attempts, group_id, item.get('gist')))
            continue
        kept.append({**item, 'attempts': attempts})
        logs.append('群发言承诺本回合未兑现，保留 群=%s 第 %d 次' % (group_id, attempts))
    state['group_promises'] = kept
    return state, logs


def promise_preamble(promises: list[dict[str, Any]]) -> Optional[str]:
    """群回合批次开头的「你答应过的事」。"""
    if not promises:
        return None
    lines = ['[你答应过的事] 你刚才在私聊里答应了：']
    for item in promises:
        lines.append('· 答应 %s：在这个群里说「%s」' % (item.get('from') or '对方', item.get('gist')))
    lines.append('这一回合就在群里用你自己的话把它说出来（可以害羞、可以顺口自嘲一句，但要真的发出来，'
                 '不要只在心里想）；群里正在聊的别的事可以顺带回，也可以不回。')
    return '\n'.join(lines)
