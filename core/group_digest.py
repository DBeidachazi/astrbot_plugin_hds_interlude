"""本地扩展：群聊批次的 @ 渲染与「谁在找她」摘要。

用户反馈（2026-10-04）：「群聊里这样 @ 他之后，他好像不知道上下文」。复盘：冷却期间两位群友
各说各的（一个追问 F 和弦、一个让她介绍提瓦特），4 条消息进了同一批；模型其实分清了两个人，
但只能回一条消息，于是把两件事揉进一句、还跳过了「介绍一下」的真实请求。另外：

- 消息正文里的 `<at id="1690619901"/>` 原样交给模型，模型要自己猜那串数字是谁；
- 批次只是按顺序平铺，没有告诉模型「这一批有几个人在找你、各自想要什么」。

这里把 @ 渲染成「@林小满」/「@群友名」，并在**多人同批**时生成一段摘要放在批次前面。
本模块是纯函数，不改动入库的原文（只影响发给模型的这一份）。
"""

from __future__ import annotations

import re
from typing import Any, Iterable, Optional

_AT = re.compile(r'<at\b[^>]*?\bid\s*=\s*["\']?([^"\'\s/>]+)["\']?[^>]*?/?>(?:\s*</at>)?', re.IGNORECASE)
_AT_NAME = re.compile(r'\bname\s*=\s*["\']([^"\']*)["\']', re.IGNORECASE)
_TAG = re.compile(r'<[^>]+>')
_PLACEHOLDER = re.compile(r'^\s*(\[[^\]]{1,12}\]\s*)*$')   # [图片] [动画表情] [语音] …
_SNIPPET = 40


def _get(record: Any, *keys: str) -> Any:
    if not isinstance(record, dict):
        return None
    for key in keys:
        if record.get(key) is not None:
            return record[key]
    return None


def name_map(messages: Iterable[Any]) -> dict[str, str]:
    """群上下文 / 批次里出现过的 发言人 id → 昵称。"""
    names: dict[str, str] = {}
    for message in messages or []:
        sender = str(_get(message, 'senderId', 'sender_id') or '').strip()
        name = str(_get(message, 'senderName', 'sender_name') or '').strip()
        if sender and name and name != sender:
            names[sender] = name
        quote = _get(message, 'quote')
        q_sender = str(_get(quote, 'senderId', 'sender_id') or '').strip()
        q_name = str(_get(quote, 'senderName', 'sender_name') or '').strip()
        if q_sender and q_name and q_name != q_sender:
            names.setdefault(q_sender, q_name)
        for match in _AT.finditer(str(_get(message, 'content') or '')):
            tag_name = _AT_NAME.search(match.group(0))
            if tag_name and tag_name.group(1).strip():
                names.setdefault(match.group(1).strip(), tag_name.group(1).strip())
    return names


def render_mentions(text: Any, names: dict[str, str], self_ids: Iterable[str], character_name: str) -> str:
    """`<at id="X"/>` → 「@林小满」（她自己）/「@群友名」/「@全体成员」/「@群友」。"""
    own = {str(item) for item in self_ids if str(item or '').strip()}

    def repl(match: re.Match) -> str:
        target = match.group(1).strip()
        if target in own:
            label = character_name or '她'
        elif target.lower() == 'all':
            label = '全体成员'
        else:
            # 标签自带的显示名（适配层写入）优先；都不认识时用「其他群友」——
            # 原来的「@群友」会让模型以为是在叫自己（2026-10-04「主播」误认）。
            tag_name = _AT_NAME.search(match.group(0))
            label = names.get(target) or (tag_name.group(1).strip() if tag_name else '') or '其他群友'
        return '@%s ' % label

    rendered = _AT.sub(repl, str(text or ''))
    rendered = re.sub(r'</at\s*>', '', rendered, flags=re.IGNORECASE)  # <at id=…>文字</at> 的闭合标签
    return re.sub(r'[ \t]{2,}', ' ', rendered).strip()


def _snippet(text: str) -> str:
    text = ' '.join(str(text or '').split())
    return text if len(text) <= _SNIPPET else text[:_SNIPPET] + '…'


def _mentions_her(message: Any, self_ids: set[str], character_name: str) -> bool:
    raw = str(_get(message, 'content') or '')
    if any(match.group(1).strip() in self_ids for match in _AT.finditer(raw)):
        return True
    return bool(character_name) and ('@' + character_name) in raw


def batch_digest(batch: list[Any], self_ids: Iterable[str], character_name: str,
                 names: Optional[dict[str, str]] = None) -> Optional[str]:
    """多人同批时的摘要；只有一位发言人时返回 None（不需要）。"""
    own = {str(item) for item in self_ids if str(item or '').strip()}
    names = names or name_map(batch)
    order: list[str] = []
    people: dict[str, dict[str, Any]] = {}
    for message in batch or []:
        sender = str(_get(message, 'senderId', 'sender_id') or '').strip() or '?'
        if sender in own:
            continue
        if sender not in people:
            order.append(sender)
            people[sender] = {'name': names.get(sender) or _get(message, 'senderName', 'sender_name') or '群友',
                              'count': 0, 'mentions': 0, 'quotes': [], 'last': ''}
        info = people[sender]
        info['count'] += 1
        if _mentions_her(message, own, character_name):
            info['mentions'] += 1
        quote = _get(message, 'quote')
        if str(_get(quote, 'senderId', 'sender_id') or '') in own:
            quoted = _get(quote, 'content', 'text')
            if quoted:
                info['quotes'].append(_snippet(quoted))
        info['last'] = _snippet(render_mentions(_get(message, 'content'), names, own, character_name))
    if len(order) < 2:
        return None
    lines = ['[本批概况] 这一批共 %d 条，来自 %d 位群友：' % (sum(p['count'] for p in people.values()), len(order))]
    for sender in order:
        info = people[sender]
        parts = ['%d 条' % info['count']]
        if info['mentions']:
            parts.append('其中 %d 条 @ 了你' % info['mentions'])
        if info['quotes']:
            parts.append('引用了你说的「%s」' % info['quotes'][-1])
        lines.append('· %s：%s；最后一句：「%s」' % (info['name'], '，'.join(parts), info['last']))
    lines.append('不同的人在说不同的事：分开回应（分气泡、开头带「@名字」，或引用对方那条），'
                 '不要揉成一句；别人明确提出的请求要正面回应，不能只挑个错字就算回过了。')
    return '\n'.join(lines)


# ---------------------------------------------------------------- 对象识别（2026-10-04）
# 现场：群友 A 发「@好小狗 主播主播有空可以买个星战前线2玩玩」+「全是梗」，她以为「主播」在叫自己，
# 接了一句「哪有主播啦」。原因：at 标签只有 id（显示名被适配层丢了）→ 渲染成「@群友」；
# 提示词里也没有「这条在对别人说」的信息，always 模式下意愿 0.924 照常触发。

def _targets(message: Any) -> list[str]:
    return [match.group(1).strip() for match in _AT.finditer(str(_get(message, 'content') or ''))]


def _is_placeholder(message: Any) -> bool:
    """只有图片 / 表情 / 语音等占位、没有文字的消息：不参与对象判断。"""
    text = _TAG.sub('', str(_get(message, 'content') or ''))
    return bool(_PLACEHOLDER.match(text))


def _calls_her(message: Any, self_ids: set[str], aliases: Iterable[str]) -> bool:
    if any(target in self_ids for target in _targets(message)):
        return True
    if str(_get(_get(message, 'quote'), 'senderId', 'sender_id') or '') in self_ids:
        return True
    text = _TAG.sub('', str(_get(message, 'content') or ''))
    return any(alias and alias in text for alias in aliases)


def message_audiences(batch: list[Any], self_ids: Iterable[str], aliases: Iterable[str] = (),
                      prior: Iterable[Any] = ()) -> list[Optional[str]]:
    """逐条判断「这条在对谁说」：'her' / 'other:<id>' / None（对群里所有人，没有明确对象）。

    同一个人紧接着发的、不带 @ 的补充（如「全是梗」）沿用他上一条的对象；
    `prior`（批次之前的群上下文）用来延续跨批次的对象。占位消息（只有图片等）沿用不改写。
    """
    own = {str(item) for item in self_ids if str(item or '').strip()}
    aliases = [str(item) for item in aliases if str(item or '').strip()]
    last: dict[str, Optional[str]] = {}

    def classify(message: Any) -> Optional[str]:
        sender = str(_get(message, 'senderId', 'sender_id') or '').strip()
        if _calls_her(message, own, aliases):
            result: Optional[str] = 'her'
        else:
            others = [target for target in _targets(message) if target not in own and target.lower() != 'all']
            quoted = str(_get(_get(message, 'quote'), 'senderId', 'sender_id') or '').strip()
            if others:
                result = 'other:' + others[0]
            elif quoted and quoted != sender:
                result = 'other:' + quoted
            elif _targets(message) or _is_placeholder(message):
                result = None if _targets(message) else last.get(sender)
            else:
                result = last.get(sender)
        if sender:
            last[sender] = result
        return result

    for message in prior or []:
        if str(_get(message, 'senderId', 'sender_id') or '') not in own:
            classify(message)
    return [classify(message) for message in batch or []]


def audience_note(audience: Optional[str], names: dict[str, str]) -> str:
    """附在批次每条消息后面的一行说明；对象不明确时不加。"""
    if audience == 'her':
        return '（↑ 这条是在找你）'
    if audience and audience.startswith('other:'):
        target = audience[len('other:'):]
        return '（↑ 这条是对 @%s 说的，不是对你；里面的称呼都指对方）' % (names.get(target) or '其他群友')
    return ''


def addressed_elsewhere_only(batch: list[Any], self_ids: Iterable[str], aliases: Iterable[str] = (),
                             prior: Iterable[Any] = ()) -> bool:
    """整批有文字的消息全都在对别人说（没有一条找她、也没有一条泛泛对全群）→ True。"""
    audiences = message_audiences(batch, self_ids, aliases, prior)
    texts = [audience for message, audience in zip(batch or [], audiences) if not _is_placeholder(message)]
    return bool(texts) and all(audience and audience.startswith('other:') for audience in texts)
