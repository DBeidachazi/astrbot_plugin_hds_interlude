"""本地扩展：长线剧情（Story Arcs）。

把零散的生活钩子与她自己的计划串成多日主线（如「F和弦与文艺汇演」），让“像小说”的感觉
来自**有方向的变化**。

- 主线定义在 `plugin_data/astrbot_plugin_hds_interlude/story_arcs.json`（热读取），
  缺失时用本文件内置的默认主线。
- 运行状态放在 story state 的 `extensions.vitality.arcs`，而**不是** `interlude_arc` 表：
  那张表归记忆压缩所有（`chunk7` 创建、`chunk8` 自动改写摘要，同时只有一条 active），
  把剧情主线写进去会被压缩流程覆盖。
- 推进以剧本证据为准：模型回报 `arcProgress:[{"id","quote"}]`，宿主确认 quote 确实出现在
  本回合原文里、且本阶段已满最短天数，才前进一个阶段。每天最多推进一步。
- 本模块是纯函数，不碰数据库与日历。
"""

from __future__ import annotations

import hashlib
import json
import os
import random
from datetime import date, datetime
from pathlib import Path
from typing import Any, Optional

DEFAULT_CONFIG: dict[str, Any] = {
    'enabled': True,
    'max_active': 2,
    # 没有活跃主线的休息日里，自发开启一条主线的概率。
    'spontaneous_chance': 0.15,
    'arcs': [
        {'id': 'guitar-f-chord', 'title': 'F和弦与文艺汇演',
         'stages': ['卡在F和弦上，手指按得生疼，有点想放弃', '江晚学姐看不下去，教了她按横按的小窍门',
                    '被苏棠怂恿着报名了校园文艺汇演', '紧张地排练，有一次在大家面前弹错了', '站上汇演的舞台'],
         'min_days_per_stage': 2, 'spontaneous': True},
        {'id': 'stray-cat', 'title': '楼下的小橘猫',
         'stages': ['在楼下发现一只脏兮兮的小橘猫', '每天偷偷带吃的下楼喂它', '被妈妈发现了',
                    '和妈妈、苏棠一起帮它找领养人', '小橘猫有了新家'],
         'min_days_per_stage': 1, 'spontaneous': False},
        {'id': 'lost-notebook', 'title': '长椅上的笔记本',
         'stages': ['捡到一本夹着旧车票和手写便签的笔记本', '按便签上的线索猜主人是谁', '和苏棠一起去车票上的地方看看',
                    '找到了笔记本的主人，听到一个温暖的小故事'],
         'min_days_per_stage': 1, 'spontaneous': False},
        {'id': 'monthly-exam', 'title': '月考逆袭物理',
         'stages': ['物理小测考砸了，心情低落', '下决心每天刷两道物理大题，周屿偶尔给她讲题',
                    '月考前紧张地复习', '月考物理进步了，开心得想请苏棠喝奶茶'],
         'min_days_per_stage': 2, 'spontaneous': True, 'days': ['school']},
    ],
}

#: 已完成主线保留多少条给上下文（让她能自然提起“经历过的事”）。
PAST_ARCS_IN_CONTEXT = 3


def _config_path() -> Path:
    env = os.environ.get('HDSI_STORY_ARCS_CONFIG')
    if env:
        return Path(env)
    return Path(__file__).resolve().parents[3] / 'plugin_data' / 'astrbot_plugin_hds_interlude' / 'story_arcs.json'


_cache: dict[str, Any] = {'mtime': None, 'value': None}


def load_config() -> dict[str, Any]:
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
        except Exception:  # noqa: BLE001
            pass
    _cache.update(mtime=mtime, value=cfg)
    return cfg


def _definitions(cfg: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        arc['id']: arc for arc in cfg.get('arcs') or []
        if isinstance(arc, dict) and arc.get('id') and isinstance(arc.get('stages'), list) and arc['stages']
    }


def _days_between(start: Any, day: date) -> int:
    try:
        return (day - date.fromisoformat(str(start))).days
    except ValueError:
        return 0


def active_count(state: dict[str, Any]) -> int:
    return sum(1 for item in state.values() if isinstance(item, dict) and item.get('status') == 'active')


def start_arc(state: Any, arc_id: str, day: date, reason: str,
              cfg: Optional[dict[str, Any]] = None) -> tuple[dict[str, Any], bool]:
    """开启一条主线（已开启过、超过并发上限或未定义时不开）。返回 `(新状态, 是否开启)`。"""
    cfg = cfg or load_config()
    result = dict(state) if isinstance(state, dict) else {}
    definition = _definitions(cfg).get(arc_id)
    if not cfg.get('enabled', True) or definition is None or arc_id in result:
        return result, False
    if active_count(result) >= int(cfg.get('max_active', 2) or 0):
        return result, False
    result[arc_id] = {
        'status': 'active', 'stage': 0, 'stageSince': day.isoformat(), 'startedAt': day.isoformat(),
        'reason': reason[:120], 'history': [],
    }
    return result, True


def maybe_spontaneous(story_id: str, state: Any, day: date, category: str,
                      cfg: Optional[dict[str, Any]] = None) -> Optional[str]:
    """没有活跃主线时，按当天确定性的概率挑一条可自发开启的主线 id（不修改状态）。"""
    cfg = cfg or load_config()
    state = state if isinstance(state, dict) else {}
    if not cfg.get('enabled', True) or active_count(state) > 0 or category == 'exam':
        return None
    seed = hashlib.sha256(('arc|%s|%s' % (story_id, day.isoformat())).encode('utf-8')).hexdigest()
    rng = random.Random(int(seed[:16], 16))
    if rng.random() >= float(cfg.get('spontaneous_chance', 0) or 0):
        return None
    pool = [
        arc for arc in _definitions(cfg).values()
        if arc.get('spontaneous') and arc['id'] not in state
        and (not arc.get('days') or category in arc['days'] or ('off' in arc['days'] and category != 'school'))
    ]
    if not pool:
        return None
    return rng.choice(sorted(pool, key=lambda arc: arc['id']))['id']


def context(state: Any, day: date, cfg: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    """发给模型的 `activeArcs` / `pastArcs`。"""
    cfg = cfg or load_config()
    state = state if isinstance(state, dict) else {}
    definitions = _definitions(cfg)
    active, past = [], []
    for arc_id, item in state.items():
        definition = definitions.get(arc_id)
        if not isinstance(item, dict) or definition is None:
            continue
        stages = definition['stages']
        if item.get('status') == 'active':
            index = max(0, min(int(item.get('stage', 0) or 0), len(stages) - 1))
            days_in_stage = _days_between(item.get('stageSince'), day)
            active.append({
                'id': arc_id, 'title': definition.get('title', arc_id),
                'stage': stages[index], 'stageIndex': index + 1, 'totalStages': len(stages),
                'nextBeat': stages[index + 1] if index + 1 < len(stages) else None,
                'daysInStage': days_in_stage,
                'canAdvance': days_in_stage >= int(definition.get('min_days_per_stage', 1) or 0)
                and item.get('lastAdvancedOn') != day.isoformat(),
            })
        elif item.get('status') == 'completed':
            past.append((str(item.get('completedAt') or ''), definition.get('title', arc_id)))
    past.sort(reverse=True)
    result: dict[str, Any] = {}
    if active:
        result['activeArcs'] = active
    if past:
        result['pastArcs'] = [title for _, title in past[:PAST_ARCS_IN_CONTEXT]]
    return result


def _normalized(text: str) -> str:
    return ''.join(str(text or '').split())


def apply_progress(state: Any, progress: Any, script: str, day: date,
                   cfg: Optional[dict[str, Any]] = None) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """按模型回报推进主线。quote 必须逐字（忽略空白）出现在本回合剧本里。

    `progress` 形如 `[{"id":"guitar-f-chord","quote":"...","outcome":"advanced|dropped"}]`；
    `dropped` 表示她明确放下了这条线。返回 `(新状态, 变化列表)`。
    """
    cfg = cfg or load_config()
    result = {key: dict(value) for key, value in (state or {}).items() if isinstance(value, dict)} \
        if isinstance(state, dict) else {}
    definitions = _definitions(cfg)
    prose = _normalized(script)
    changes: list[dict[str, Any]] = []
    for item in progress if isinstance(progress, list) else []:
        if not isinstance(item, dict):
            continue
        arc_id = item.get('id')
        current = result.get(arc_id)
        definition = definitions.get(arc_id)
        if not current or definition is None or current.get('status') != 'active':
            continue
        quote = _normalized(item.get('quote') or '')
        if len(quote) < 4 or quote not in prose:
            continue
        stages = definition['stages']
        if item.get('outcome') == 'dropped':
            current.update(status='dropped', endedAt=day.isoformat())
            changes.append({'id': arc_id, 'change': 'dropped'})
            continue
        if current.get('lastAdvancedOn') == day.isoformat():
            continue
        if _days_between(current.get('stageSince'), day) < int(definition.get('min_days_per_stage', 1) or 0):
            continue
        history = list(current.get('history') or [])[-10:]
        history.append({'stage': int(current.get('stage', 0)), 'on': day.isoformat(), 'quote': quote[:120]})
        current['history'] = history
        current['lastAdvancedOn'] = day.isoformat()
        next_stage = int(current.get('stage', 0)) + 1
        if next_stage >= len(stages):
            current.update(status='completed', completedAt=day.isoformat())
            changes.append({'id': arc_id, 'change': 'completed', 'title': definition.get('title')})
        else:
            current.update(stage=next_stage, stageSince=day.isoformat())
            changes.append({'id': arc_id, 'change': 'advanced', 'stage': stages[next_stage]})
    return result, changes
