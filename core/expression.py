"""本地扩展：颜文字频率控制（Expression Budget）。

平衡版人设上线后，她 91% 的消息句尾都挂着颜文字，且只在 (｡･ω･｡) 与 (＞﹏＜) 两个之间轮换：
模型照抄了人设里的示例，又在上下文里模仿自己最近的消息，而且不知道自己用了多少。
纯提示词会飘（上一版 0%、这一版 91%），纯正则硬删不看情绪、还会让剧本与实际消息对不上。
所以分三层：

1. 去锚定：人设不再列具体颜文字（部署脚本里改，不在本模块）；
2. 软反馈：每回合按她最近 N 条消息算出 `interval.expressionBudget`（free / sparing / rest +
   最近用过的颜文字），可随 Alter 氛围把目标 ±0.1；
3. 硬兜底：只有超过窗口上限、本轮应休息、或与上一条用了同一个颜文字时，才删掉**句尾或独立成段**
   的颜文字，句中文字一个字不动，并在剧本原文里同步改同一段，保证「她发出去的话」与「剧本里写她
   打的字」一致。

配置在 `plugin_data/astrbot_plugin_hds_interlude/expression.json`（热读取，改完下一回合生效）。
本模块是纯函数，不碰数据库。
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any, Optional

DEFAULT_CONFIG: dict[str, Any] = {
    'enabled': True,
    'kaomoji': {
        # 期望带颜文字的消息比例（0.3 ≈ 10 条里 3 条）。
        'target_rate': 0.3,
        # 统计她最近多少条消息。
        'window': 10,
        # 窗口内硬上限：超过才由硬兜底删除。
        'max_in_window': 4,
        # 窗口使用率达到多少进入 sparing（只有情绪很明显时才用）。
        'sparing_at': 0.25,
        # 同一个颜文字不连着用；上一条刚用过时本条 rest。
        'avoid_repeat': True,
        # 按 Alter 氛围把目标 ±0.1（轻快 +、沉重 −）。
        'mood_adaptive': True,
        # 硬兜底开关；关掉只剩软反馈。
        'hard_guard': True,
    },
    'opener': {
        # 开场白口癖：最近窗口里以「？」开头的消息达到上限时，软提示换个开法（不硬删）。
        'enabled': True,
        'window': 10,
        'max_in_window': 4,
        'prefixes': ['？', '?'],
        # 硬兜底：上一条已经以问号开头，或窗口内问号开头达到上限时，发出前剪掉开头的问号
        # （只剪开头那串问号，正文不动），并同步剧本原文，打断上下文里的自我模仿。
        'hard_guard': True,
    },
    'exclamation': {
        # 感叹号只做软反馈（不硬删：删感叹号会改变句子语气），默认关。
        'enabled': False,
        'target_rate': 0.4,
        'window': 10,
        'max_in_window': 6,
    },
}

# ---------------------------------------------------------------------------- 识别

# 括号式颜文字里常见的「脸部」字符；括号内必须至少有一个，且不能有汉字，
# 这样「（周六补课）」「(3/5)」「(PDF)」这类普通括号不会被误认。
_FACE_CHARS = (
    'ωω･・｡ﾟ゜°˘ᴗ•∀∇▽□△◇○◎●⊙ㅂ﹏＞＜><^＾´`￣ー＿_；;ε∠з˙꒳ᵕ⁄≧≦ಥ╥TＴ'
    'ⅴ∪ᗜ○ᐛ✧♡❤♪〃✿'
)
_BRACKET = re.compile(r'[（(]([^（）()\n]{1,16})[)）]')
_CJK = re.compile(r'[一-鿿]')
_TOKEN = re.compile(r'(?<![A-Za-z])(?:qwq|qaq|tat|twt|awa|owo|ovo|t_t|t\.t|=w=|>_<|>w<|\^_\^|\^o\^|xd)(?![A-Za-z])', re.IGNORECASE)
_LYING = re.compile(r'_\(:[^\s]{1,6}\)_')
# 颜文字两侧常见的装饰与手势（如 (๑•̀ㅂ•́)و✧），连同颜文字一起算。
_DECOR = 'و✧✨♪～~ノﾉ☆★💦'


def _is_bracket_face(inner: str) -> bool:
    if _CJK.search(inner):
        return False
    if re.fullmatch(r'[\sA-Za-z0-9.,:/%+\-]*', inner):
        return False  # 纯字母数字标点：(PDF)、(3/5)、(10%)
    return any(ch in _FACE_CHARS for ch in inner)


def find_kaomoji(text: str) -> list[tuple[int, int, str]]:
    """返回 `[(start, end, 颜文字)]`，按出现顺序，区间不重叠。"""
    text = str(text or '')
    spans: list[tuple[int, int]] = []
    for pattern in (_LYING, _TOKEN):
        spans += [(m.start(), m.end()) for m in pattern.finditer(text)]
    for match in _BRACKET.finditer(text):
        if _is_bracket_face(match.group(1)):
            spans.append((match.start(), match.end()))
    spans.sort()
    merged: list[tuple[int, int]] = []
    for start, end in spans:
        # 把紧贴着的装饰（و✧ 等）并进来
        while start > 0 and text[start - 1] in _DECOR:
            start -= 1
        while end < len(text) and text[end] in _DECOR:
            end += 1
        if merged and start < merged[-1][1]:
            merged[-1] = (merged[-1][0], max(end, merged[-1][1]))
        else:
            merged.append((start, end))
    return [(start, end, text[start:end]) for start, end in merged]


def has_kaomoji(text: str) -> bool:
    return bool(find_kaomoji(text))


def strip_trailing_kaomoji(bubble: str) -> str:
    """删掉气泡末尾（以及只剩颜文字时整个气泡）的颜文字；句中的保留。"""
    text = str(bubble or '')
    while True:
        found = find_kaomoji(text)
        if not found:
            return text.strip()
        start, end, _face = found[-1]
        tail = text[end:]
        if tail.strip(' \t！!？?~～。.，,…'):
            return text.strip()  # 最后一个颜文字后面还有文字：它在句中，不动
        text = (text[:start].rstrip() + tail.strip()).rstrip()  # 保留颜文字后面的标点，去掉中间的空格
        if not text.strip():
            return ''


# ---------------------------------------------------------------------------- 配置


def _config_path() -> Path:
    env = os.environ.get('HDSI_EXPRESSION_CONFIG')
    if env:
        return Path(env)
    return Path(__file__).resolve().parents[3] / 'plugin_data' / 'astrbot_plugin_hds_interlude' / 'expression.json'


_cache: dict[str, Any] = {'mtime': None, 'value': None}


def load_config() -> dict[str, Any]:
    """热读取；缺失或损坏时用默认值。二级分组按键浅合并。"""
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
                for key, value in raw.items():
                    if key in cfg and isinstance(cfg[key], dict) and isinstance(value, dict):
                        cfg[key].update({k: v for k, v in value.items() if k in cfg[key]})
                    elif key in cfg:
                        cfg[key] = value
        except Exception:  # noqa: BLE001 - 配置坏了用默认值
            pass
    _cache.update(mtime=mtime, value=cfg)
    return cfg


# ---------------------------------------------------------------------------- 统计与额度


def _get(record: Any, *keys: str) -> Any:
    if not isinstance(record, dict):
        return None
    for key in keys:
        if record.get(key) is not None:
            return record[key]
    return None


def her_messages(entries: Any) -> list[str]:
    """她实际发出的消息（时间顺序）。"""
    items = []
    for entry in entries if isinstance(entries, list) else []:
        if _get(entry, 'kind') in ('character-message', 'character-group-message'):
            content = _get(entry, 'content')
            if isinstance(content, str) and content.strip():
                items.append((str(_get(entry, 'occurredAt', 'occurred_at') or ''), content))
    items.sort(key=lambda item: item[0])
    return [content for _, content in items]


def _alter_value(story_state: Any) -> float:
    alter = _get(story_state, 'alter_system', 'alterSystem')
    value = _get(alter, 'alter_value', 'alterValue')
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def window_stats(messages: list[str], window: int) -> dict[str, Any]:
    recent = messages[-max(1, int(window)):]
    used = [find_kaomoji(text) for text in recent]
    faces: list[str] = []
    for found in reversed(used):
        for _s, _e, face in reversed(found):
            if face not in faces:
                faces.append(face)
    return {
        'count': sum(1 for found in used if found),
        'size': len(recent),
        'last_used': bool(used and used[-1]),
        'last_faces': [face for _s, _e, face in used[-1]] if used and used[-1] else [],
        'recent_faces': faces[:3],
    }


def kaomoji_budget(messages: list[str], story_state: Any = None,
                   cfg: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    """本轮颜文字额度：`{state, recentRate, target, cap, count, avoid}`。"""
    cfg = (cfg or load_config()).get('kaomoji') or DEFAULT_CONFIG['kaomoji']
    window = int(cfg.get('window', 10) or 10)
    base = float(cfg.get('target_rate', 0.3) or 0.0)
    target = base
    cap = int(cfg.get('max_in_window', 4) or 0)
    if cfg.get('mood_adaptive', True):
        alter = _alter_value(story_state)
        if alter <= -2:      # 氛围轻快
            target, cap = base + 0.1, cap + 1
        elif alter >= 2:     # 氛围沉重
            target, cap = max(0.0, base - 0.1), max(0, cap - 1)
    stats = window_stats(messages, window)
    rate = stats['count'] / window  # 消息还不满一个窗口时按整窗计，避免一开始就判满
    if stats['count'] >= cap:
        state = 'rest'
    elif cfg.get('avoid_repeat', True) and stats['last_used']:
        state = 'rest'
    elif rate >= float(cfg.get('sparing_at', 0.25) or 0.0):
        state = 'sparing'
    else:
        state = 'free'
    return {
        'state': state, 'recentRate': round(stats['count'] / max(1, stats['size']), 2),
        'target': round(target, 2), 'cap': cap, 'count': stats['count'],
        'avoid': stats['recent_faces'], 'last_faces': stats['last_faces'],
    }


def exclamation_hint(messages: list[str], cfg: Optional[dict[str, Any]] = None) -> Optional[str]:
    """感叹号软反馈（默认关）：用太多时返回 'sparing'，否则 None。"""
    cfg = (cfg or load_config()).get('exclamation') or {}
    if not cfg.get('enabled'):
        return None
    recent = messages[-max(1, int(cfg.get('window', 10) or 10)):]
    count = sum(1 for text in recent if '！' in text or '!' in text)
    return 'sparing' if count >= int(cfg.get('max_in_window', 6) or 0) else None


def opener_hint(messages: list[str], cfg: Optional[dict[str, Any]] = None) -> Optional[str]:
    """开场白软反馈：最近窗口里以「？」等开头的消息太多时返回 'vary'，否则 None。"""
    cfg = (cfg or load_config()).get('opener') or {}
    if not cfg.get('enabled', True):
        return None
    prefixes = tuple(str(p) for p in (cfg.get('prefixes') or ['？', '?']) if str(p))
    recent = messages[-max(1, int(cfg.get('window', 10) or 10)):]
    count = sum(1 for text in recent if text.lstrip().startswith(prefixes))
    return 'vary' if count >= int(cfg.get('max_in_window', 4) or 0) else None


def prompt_budget(messages: list[str], story_state: Any = None,
                  cfg: Optional[dict[str, Any]] = None) -> Optional[dict[str, Any]]:
    """发给模型的 `interval.expressionBudget`；功能关闭时 None。"""
    cfg = cfg or load_config()
    if not cfg.get('enabled', True):
        return None
    budget = kaomoji_budget(messages, story_state, cfg)
    result: dict[str, Any] = {
        'kaomoji': budget['state'], 'recentKaomojiRate': budget['recentRate'],
        'targetKaomojiRate': budget['target'],
    }
    if budget['avoid']:
        result['avoid'] = budget['avoid']
    exclamation = exclamation_hint(messages, cfg)
    if exclamation:
        result['exclamation'] = exclamation
    opener = opener_hint(messages, cfg)
    if opener:
        result['opener'] = opener
    return result


# ---------------------------------------------------------------------------- 硬兜底


def _outgoing_slots(decision: dict[str, Any]) -> list[tuple[dict[str, Any], str]]:
    """决策里所有「会发出去的文字」所在的 (dict, 键)。"""
    slots: list[tuple[dict[str, Any], str]] = []
    for key in ('groupReply', 'group_reply'):
        value = decision.get(key)
        if isinstance(value, dict) and value.get('mode') == 'immediate' and isinstance(value.get('content'), str):
            slots.append((value, 'content'))
    interaction = decision.get('interaction')
    reply = interaction.get('reply') if isinstance(interaction, dict) else None
    if isinstance(reply, dict) and reply.get('mode') in ('immediate', 'delayed') and isinstance(reply.get('content'), str):
        slots.append((reply, 'content'))
    for key in ('crossConversationActions', 'cross_conversation_actions'):
        for action in decision.get(key) or []:
            if isinstance(action, dict) and action.get('mode') in ('immediate', 'delayed') and isinstance(action.get('content'), str):
                slots.append((action, 'content'))
    # 同一个 dict 可能以两种拼写出现两次：去重
    seen: set[int] = set()
    unique = []
    for holder, key in slots:
        if id(holder) not in seen:
            seen.add(id(holder))
            unique.append((holder, key))
    return unique


def regulate_message(content: str, separator: str) -> str:
    """把一条消息的所有气泡都去掉句尾 / 独立颜文字；全空时保留原文（不能发空消息）。"""
    bubbles = content.split(separator) if separator and separator in content else [content]
    kept = [strip_trailing_kaomoji(bubble) for bubble in bubbles]
    kept = [bubble for bubble in kept if bubble]
    return separator.join(kept) if kept else content


def _sync_script(decision: dict[str, Any], replacements: list[tuple[str, str]], separator: str) -> None:
    """在剧本原文里把原气泡换成处理后的气泡，并同步 authored_actions（原地修改，保持列表身份）。"""
    script = decision.get('script')
    if not isinstance(script, str):
        return

    def apply(text: str) -> str:
        for old, new in replacements:
            if not old or old == new:
                continue
            if new:
                if old in text:
                    text = text.replace(old, new, 1)
            else:  # 整个气泡只有颜文字：连同相邻的分隔符一起去掉
                for pattern in (separator + old, old + separator, old):
                    if pattern and pattern in text:
                        text = text.replace(pattern, '', 1)
                        break
        return text

    decision['script'] = script = apply(script)
    for key in ('authored_actions', 'authoredActions'):
        actions = decision.get(key)
        if not isinstance(actions, list):
            continue
        for index, action in enumerate(actions):
            if not isinstance(action, dict) or not isinstance(action.get('content'), str):
                continue
            content = apply(action['content'])
            if content != action['content']:
                start = script.find(content)
                actions[index] = {**action, 'content': content,
                                  **({'start': start, 'end': start + len(content)} if start >= 0 else {})}


_LEADING_QUESTION = re.compile(r'^\s*[？?][？?！!]*\s*')


def starts_with_question(text: str, prefixes: tuple[str, ...] = ('？', '?')) -> bool:
    return str(text or '').lstrip().startswith(prefixes)


def strip_leading_questions(bubble: str) -> str:
    """剪掉气泡开头那串问号（含紧跟的「！」），正文保留。"""
    return _LEADING_QUESTION.sub('', str(bubble or ''), count=1).strip()


def _opener_should_strip(history: list[str], ocfg: dict[str, Any]) -> tuple[bool, str, int]:
    prefixes = tuple(str(p) for p in (ocfg.get('prefixes') or ['？', '?']) if str(p))
    recent = history[-max(1, int(ocfg.get('window', 10) or 10)):]
    count = sum(1 for text in recent if starts_with_question(text, prefixes))
    if history and starts_with_question(history[-1], prefixes):
        return True, 'consecutive', count
    if count >= int(ocfg.get('max_in_window', 4) or 0):
        return True, 'over-cap', count
    return False, '', count


def guard_decision(decision: Any, messages: list[str], story_state: Any = None,
                   separator: str = '<sep/>', cfg: Optional[dict[str, Any]] = None) -> list[str]:
    """硬兜底：按额度处理本回合要发出的每条消息，原地修改 decision。返回日志行。

    两个维度逐气泡处理：颜文字（句尾 / 独立颜文字）与开头问号（「？？？好家伙」→「好家伙」）。
    同一条回复可能以两种拼写各存一份（`groupReply` / `group_reply`），按原文分组，
    每条不同的消息只判定一次，结果写回所有副本；剧本原文与 authored_actions 同步修改。
    """
    cfg = cfg or load_config()
    if not isinstance(decision, dict) or not cfg.get('enabled', True):
        return []
    kcfg = cfg.get('kaomoji') or {}
    ocfg = cfg.get('opener') or {}
    kaomoji_on = bool(kcfg.get('hard_guard', True))
    opener_on = bool(ocfg.get('enabled', True) and ocfg.get('hard_guard', True))
    if not (kaomoji_on or opener_on):
        return []
    prefixes = tuple(str(p) for p in (ocfg.get('prefixes') or ['？', '?']) if str(p))
    groups: dict[str, list[tuple[dict[str, Any], str]]] = {}
    for holder, key in _outgoing_slots(decision):
        groups.setdefault(holder[key], []).append((holder, key))
    history = list(messages)
    logs: list[str] = []
    replacements: list[tuple[str, str]] = []
    for original, holders in groups.items():
        bubbles = original.split(separator) if separator and separator in original else [original]
        strip_kaomoji = False
        if kaomoji_on and find_kaomoji(original):
            budget = kaomoji_budget(history, story_state, cfg)
            repeat = bool(kcfg.get('avoid_repeat', True)) and any(
                face in budget['last_faces'] for _s, _e, face in find_kaomoji(original))
            over = budget['count'] + 1 > budget['cap']
            if budget['state'] == 'rest' or over or repeat:
                strip_kaomoji = True
                reason = 'rest' if budget['state'] == 'rest' else ('over-cap' if over else 'repeat')
                logs.append('表达频控：去掉颜文字 原因=%s 窗口=%d/%d 原文=%s' % (
                    reason, budget['count'], budget['cap'], original[:60]))
        strip_opener = False
        if opener_on and any(starts_with_question(bubble, prefixes) for bubble in bubbles):
            strip_opener, reason, count = _opener_should_strip(history, ocfg)
            if strip_opener:
                logs.append('表达频控：剪掉开头问号 原因=%s 窗口=%d/%d 原文=%s' % (
                    reason, count, int(ocfg.get('max_in_window', 4) or 0), original[:60]))
        final = original
        if strip_kaomoji or strip_opener:
            transformed = []
            for bubble in bubbles:
                new = strip_trailing_kaomoji(bubble) if strip_kaomoji else bubble.strip()
                if strip_opener and starts_with_question(new, prefixes):
                    new = strip_leading_questions(new)
                transformed.append((bubble.strip(), new))
            kept = [new for _old, new in transformed if new]
            if kept:  # 全部剪空（例如整条只有「？？？」）时保留原文，绝不发空消息
                final = separator.join(kept) if len(bubbles) > 1 else kept[0]
                replacements += [(old, new) for old, new in transformed if old != new]
        for holder, key in holders:
            holder[key] = final
        history.append(final)
    if replacements:
        _sync_script(decision, replacements, separator)
    return logs
