"""现实日历驱动（本地扩展，非上游移植）。

让主角的作息跟随中国大陆现实：
* 法定节假日与调休补班：优先 `chinese_calendar`（国务院官方安排）；该库尚无数据的年份
  用 `lunarcalendar` 按农历推算春节/端午/中秋并按惯例估算假期（`estimated=True`，不含调休）。
* 高中校历：9 月 1 日开学；寒假围绕春节；暑假 7 月中旬至 8 月底；准高三暑期提前返校；
  高考 6 月 7 日起；按入学年份推算年级、按生日推算年龄。
* 产出两份东西：
  1. `narrative_context(now, timezone)` → 主叙事 `interval.realCalendar`（权威现实日历事实）；
  2. `build_preplan_record(...)` → 由校历生成的 Schedule Preplan 记录（周规律 + 例外日 + 物化日）。

配置文件（可选）：`<AstrBot data>/plugin_data/astrbot_plugin_hds_interlude/real_calendar.json`，
缺省值见 `DEFAULT_CONFIG`。纯函数、无网络；读配置文件带 mtime 缓存。
"""

from __future__ import annotations

import hashlib
import json
import os
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Optional
from zoneinfo import ZoneInfo

try:  # 官方节假日安排
    import chinese_calendar as _cc
except Exception:  # noqa: BLE001 - 缺库时降级为估算
    _cc = None

try:  # 农历换算（估算年份用）
    from lunarcalendar import Converter as _LunarConverter, Lunar as _Lunar
except Exception:  # noqa: BLE001
    _LunarConverter = None
    _Lunar = None

VERSION = 2
REASON_TAG = '[real-calendar'

DEFAULT_CONFIG: dict[str, Any] = {
    'enabled': True,
    'high_school_entry_year': 2025,      # 高一入学（9 月）年份：2025 → 2026-27 学年高二
    'birth_date': '2009-05-21',
    'term_start': '09-01',
    'summer_break_start': '07-11',       # 高一、高二暑假开始
    'rising_senior_return': '08-10',     # 准高三暑期返校补课
    'winter_break_before_cny_days': 16,  # 春节前多少天放寒假（高一高二）
    'winter_break_after_cny_days': 17,   # 春节后多少天开学（高一高二）
    'senior_winter_before_cny_days': 9,
    'senior_winter_after_cny_days': 9,
    'gaokao_start': '06-07',
    'gaokao_days': 4,                    # 新高考省份多为 6/7-6/10
    'gaokao_prep_days': 3,               # 考前自主复习（不上课）
    'saturday_classes': {'1': False, '2': False, '3': True},
    'after_graduation': '毕业后去向未定（大学生活待设定）',
    'horizon_days': 14,
    # 每日作息模板：[id, start, end, label, kind, location]；上课块 id 以 classes- 开头时自动追加补课后缀，
    # 休息日模板里的 {label} 替换为假期名称。修改 real_calendar.json 即热生效（无需重载插件）。
    'school_day_blocks': [
        ['sleep', '00:00', '06:00', '夜间睡眠', 'routine', '家'],
        ['breakfast', '06:00', '06:40', '起床洗漱、吃早饭（边吃边看看手机）', 'routine', '家'],
        ['commute-am', '06:40', '07:00', '骑车上学', 'routine', '路上'],
        ['classes-am', '07:00', '12:00', '早读与上午课程（上课时手机静音收好，课间可以看看手机、偶尔回消息）', 'fixed', '学校'],
        ['lunch', '12:00', '13:30', '午饭与午休（刷手机、回消息、群里冒泡）', 'routine', '学校'],
        ['classes-pm', '13:30', '17:00', '下午课程（课间可以看看手机）', 'fixed', '学校'],
        ['commute-pm', '17:00', '17:30', '17:00放学，骑车回家', 'routine', '路上'],
        ['dinner', '17:30', '18:30', '晚饭（边吃边看手机、聊天）', 'routine', '家'],
        ['homework', '18:30', '20:00', '写作业（效率还行，一个半小时左右写完）', 'flexible', '家'],
        ['evening-free', '20:00', '23:59', '自由时间：刷手机、追番、打游戏、水群，偶尔下楼散步买东西或练会儿吉他', 'open', ''],
    ],
    'rest_day_blocks': [
        ['sleep', '00:00', '09:00', '睡觉、赖床', 'routine', '家'],
        ['late-morning', '09:00', '12:00', '{label}：起床、吃早饭、自由安排（可能跑腿、约朋友或临时起意出门）', 'flexible', ''],
        ['lunch', '12:00', '13:00', '午饭（边吃边看手机）', 'routine', '家'],
        ['afternoon', '13:00', '17:30', '{label}：自由活动（可能约朋友出门、陪妈妈买菜、去书店或公园、在家练吉他；作业不多，挑时间写掉）', 'open', ''],
        ['dinner', '17:30', '18:30', '晚饭', 'routine', '家'],
        ['evening', '18:30', '23:59', '自由时间：可能还在外面玩、陪家人，或在家刷手机、追番、打游戏、群里闲聊', 'open', ''],
    ],
    'routine_notes': {
        'school': '上课日：7:00前到校，17:00放学，没有晚自习。上课时手机静音收好不看；课间、早中晚饭和午休时会看看手机、偶尔回消息或在群里冒个泡。晚上作业一个半小时左右写完，之后自由安排（刷手机、追番、游戏、水群）。',
        'rest': '休息日：会赖床；全天大多有空，看手机和回消息都比较随意。不会整天待在家里：常和苏棠约出门、陪妈妈买菜、去书店或江边公园，也会临时起意做点新鲜事；作业不多，挑时间写掉。',
        'holiday': '长假：作息放松，会赖床，但不会天天宅着：会和苏棠约着出去逛街探店、拍照，陪家人走亲戚或买东西，去书店、公园或机厅，也会给自己定个小目标（比如把F和弦练熟）；作业挑时间写掉。看手机和回消息都很随意。',
        'gaokao': '高考期间：专心考试，基本不看手机。',
    },
}

_HOLIDAY_ZH = {
    "New Year's Day": '元旦', 'Spring Festival': '春节', 'Tomb-sweeping Day': '清明节',
    'Labour Day': '劳动节', 'Dragon Boat Festival': '端午节', 'National Day': '国庆节',
    'Mid-autumn Festival': '中秋节', 'Anti-Fascist 70th Day': '抗战胜利纪念日',
}
_WEEKDAY_ZH = ('星期一', '星期二', '星期三', '星期四', '星期五', '星期六', '星期日')
_WEEKDAY_KEY = ('monday', 'tuesday', 'wednesday', 'thursday', 'friday', 'saturday', 'sunday')
_GRADE_ZH = {1: '高一', 2: '高二', 3: '高三'}

STATUS_ZH = {
    'school': '上课日',
    'makeup_school': '调休补课日（按工作日上课）',
    'saturday_school': '周六补课',
    'weekend': '周末休息',
    'legal_holiday': '法定节假日（放假）',
    'winter_break': '寒假',
    'summer_break': '暑假',
    'gaokao_prep': '高考前自主复习（不上课）',
    'gaokao': '高考',
    'graduation_summer': '高考结束·毕业暑假',
    'post_graduation': '高中已毕业',
    'pre_high_school': '尚未升入高中',
}
_SCHOOL_STATUSES = {'school', 'makeup_school', 'saturday_school'}


# ============================================================ 配置


def _config_path() -> Path:
    env = os.environ.get('HDSI_REAL_CALENDAR_CONFIG')
    if env:
        return Path(env)
    # <data>/plugins/astrbot_plugin_hds_interlude/core/real_calendar.py → <data>/plugin_data/...
    return Path(__file__).resolve().parents[3] / 'plugin_data' / 'astrbot_plugin_hds_interlude' / 'real_calendar.json'


_config_cache: dict[str, Any] = {'mtime': None, 'value': None}


def load_config() -> dict[str, Any]:
    path = _config_path()
    try:
        mtime = path.stat().st_mtime
    except OSError:
        mtime = None
    if _config_cache['value'] is not None and _config_cache['mtime'] == mtime:
        return _config_cache['value']
    cfg = json.loads(json.dumps(DEFAULT_CONFIG))
    if mtime is not None:
        try:
            raw = json.loads(path.read_text(encoding='utf-8'))
            if isinstance(raw, dict):
                for key, value in raw.items():
                    if key in cfg and isinstance(cfg[key], dict) and isinstance(value, dict):
                        cfg[key].update(value)
                    elif key in cfg:
                        cfg[key] = value
        except Exception:  # noqa: BLE001 - 配置损坏时使用默认值
            pass
    _config_cache.update(mtime=mtime, value=cfg)
    return cfg


def enabled(cfg: Optional[dict[str, Any]] = None) -> bool:
    return bool((cfg or load_config()).get('enabled', True))


def config_signature(cfg: dict[str, Any]) -> str:
    raw = json.dumps(cfg, sort_keys=True, ensure_ascii=False) + f'|v{VERSION}|cc={getattr(_cc, "__version__", "none")}'
    return hashlib.sha1(raw.encode('utf-8')).hexdigest()[:10]


def _md(year: int, mmdd: str) -> date:
    month, day = (int(x) for x in str(mmdd).split('-'))
    return date(year, month, day)


# ============================================================ 节假日


def _lunar_to_solar(year: int, month: int, day: int) -> Optional[date]:
    if _LunarConverter is None:
        return None
    try:
        return _LunarConverter.Lunar2Solar(_Lunar(year, month, day)).to_date()
    except Exception:  # noqa: BLE001
        return None


def _official_available(day: date) -> bool:
    if _cc is None:
        return False
    try:
        _cc.is_holiday(day)
        return True
    except Exception:  # noqa: BLE001 - NotImplementedError：该年尚无官方数据
        return False


_estimate_cache: dict[int, dict[date, str]] = {}


def _estimated_holidays(year: int) -> dict[date, str]:
    """无官方数据年份的惯例估算（不含调休补班）。"""
    if year in _estimate_cache:
        return _estimate_cache[year]
    out: dict[date, str] = {date(year, 1, 1): '元旦'}

    def span(start: Optional[date], days: int, name: str) -> None:
        if start is None:
            return
        for i in range(days):
            out.setdefault(start + timedelta(days=i), name)

    cny = _lunar_to_solar(year, 1, 1)
    span(cny - timedelta(days=1) if cny else None, 8, '春节')
    span(date(year, 4, 4), 3, '清明节')
    span(date(year, 5, 1), 5, '劳动节')
    span(_lunar_to_solar(year, 5, 5), 3, '端午节')
    mid = _lunar_to_solar(year, 8, 15)
    national_len = 7
    if mid and date(year, 9, 29) <= mid <= date(year, 10, 8):
        national_len = 8  # 中秋与国庆连休
    elif mid:
        span(mid, 3, '中秋节')
    span(date(year, 10, 1), national_len, '国庆节')
    _estimate_cache[year] = out
    return out


def holiday_info(day: date) -> dict[str, Any]:
    """{'is_holiday', 'is_makeup_workday', 'name', 'estimated'}。"""
    weekend = day.weekday() >= 5
    if _official_available(day):
        is_holiday, detail = _cc.get_holiday_detail(day)
        name = _HOLIDAY_ZH.get(getattr(detail, 'value', detail), str(getattr(detail, 'value', detail) or '')) if detail else ''
        legal = bool(is_holiday and detail)
        makeup = bool(weekend and not is_holiday)
        if makeup and name:
            name = f'{name}调休'
        return {'is_holiday': legal, 'is_makeup_workday': makeup, 'name': name, 'estimated': False}
    name = _estimated_holidays(day.year).get(day, '')
    return {'is_holiday': bool(name), 'is_makeup_workday': False, 'name': name, 'estimated': True}


def spring_festival(year: int) -> Optional[date]:
    """春节（农历正月初一）。优先官方数据中的春节假期推算，其次农历换算。"""
    lunar = _lunar_to_solar(year, 1, 1)
    if lunar:
        return lunar
    if _cc is not None:
        days = [date(year, 1, 1) + timedelta(days=i) for i in range(70)]
        hits = [d for d in days if _official_available(d) and _cc.get_holiday_detail(d)[1]
                and getattr(_cc.get_holiday_detail(d)[1], 'value', '') == 'Spring Festival']
        if hits:
            return hits[0] + timedelta(days=1)
    return None


# ============================================================ 校历


def _academic_year(day: date, cfg: dict[str, Any]) -> int:
    start = _md(day.year, cfg['term_start'])
    return day.year if day >= start else day.year - 1


def grade_of(day: date, cfg: dict[str, Any]) -> int:
    """1-3 为高一至高三；0 为入学前；4 为已毕业（高考后按 4 处理见 phase）。"""
    return _academic_year(day, cfg) - int(cfg['high_school_entry_year']) + 1


def gaokao_range(cfg: dict[str, Any]) -> tuple[date, date]:
    year = int(cfg['high_school_entry_year']) + 3
    start = _md(year, cfg['gaokao_start'])
    return start, start + timedelta(days=max(1, int(cfg['gaokao_days'])) - 1)


def _winter_break(academic_year: int, grade: int, cfg: dict[str, Any]) -> Optional[tuple[date, date]]:
    cny = spring_festival(academic_year + 1)
    if cny is None:
        cny = date(academic_year + 1, 2, 5)  # 极端兜底
    senior = grade >= 3
    before = int(cfg['senior_winter_before_cny_days' if senior else 'winter_break_before_cny_days'])
    after = int(cfg['senior_winter_after_cny_days' if senior else 'winter_break_after_cny_days'])
    start = max(cny - timedelta(days=before), date(academic_year + 1, 1, 10))
    return start, cny + timedelta(days=after)


def age_on(day: date, cfg: dict[str, Any]) -> int:
    birth = date.fromisoformat(cfg['birth_date'])
    return day.year - birth.year - ((day.month, day.day) < (birth.month, birth.day))


def classify_day(day: date, cfg: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    cfg = cfg or load_config()
    grade = grade_of(day, cfg)
    info = holiday_info(day)
    gk_start, gk_end = gaokao_range(cfg)
    prep_start = gk_start - timedelta(days=max(0, int(cfg['gaokao_prep_days'])))
    phase = ''
    status = ''
    grade_label = _GRADE_ZH.get(grade, '')

    if grade < 1:
        status, phase = 'pre_high_school', '初中/升学前'
    elif day > gk_end:
        # 高考结束：当年 9 月 1 日前为毕业暑假，之后为毕业
        grade_label = '高中毕业生'
        if day < _md(gk_end.year, cfg['term_start']):
            status, phase = 'graduation_summer', '高考结束后的毕业暑假'
        else:
            status, phase = 'post_graduation', str(cfg.get('after_graduation') or '高中已毕业')
    elif gk_start <= day <= gk_end:
        status, phase = 'gaokao', f'{gk_start.year}年高考'
    elif prep_start <= day < gk_start:
        status, phase = 'gaokao_prep', '高考前自主复习'
    else:
        ay = _academic_year(day, cfg)
        winter = _winter_break(ay, grade, cfg)
        summer_start = _md(ay + 1, cfg['summer_break_start'])
        if grade == 2 and day >= summer_start:
            rising_return = _md(ay + 1, cfg['rising_senior_return'])
            if day < rising_return:
                status, phase = 'summer_break', '高二升高三的暑假'
            else:
                phase, grade_label = '准高三暑期返校补课', '准高三'
        elif grade in (1,) and day >= summer_start:
            status, phase = 'summer_break', '高一升高二的暑假'
        elif winter and winter[0] <= day <= winter[1]:
            status, phase = 'winter_break', '寒假（春节前后）'
        if not status:
            if not phase:
                in_spring = winter is not None and day > winter[1]
                phase = f'{_GRADE_ZH.get(grade, "")}{"下" if in_spring else "上"}学期'
            weekday = day.weekday()
            if info['is_holiday']:
                status = 'legal_holiday'
            elif info['is_makeup_workday']:
                status = 'makeup_school'
            elif weekday == 5 and bool((cfg.get('saturday_classes') or {}).get(str(min(grade, 3)), False)):
                status = 'saturday_school'
            elif weekday >= 5:
                status = 'weekend'
            else:
                status = 'school'
    return {
        'date': day.isoformat(),
        'weekday': _WEEKDAY_ZH[day.weekday()],
        'status': status,
        'statusZh': STATUS_ZH.get(status, status),
        'isSchoolDay': status in _SCHOOL_STATUSES or status == 'gaokao',
        'holiday': info['name'] or None,
        'holidayEstimated': bool(info['estimated'] and info['name']),
        'officialDataAvailable': not info['estimated'],
        'grade': grade_label,
        'phase': phase,
    }


# ============================================================ 主叙事上下文


def _break_runs(start: date, cfg: dict[str, Any], limit_days: int = 200) -> Optional[dict[str, Any]]:
    """从 start 起找下一段「非普通周末」的连续假期（法定假日/寒暑假/毕业暑假）。"""
    day = start
    for _ in range(limit_days):
        c = classify_day(day, cfg)
        if c['status'] in ('legal_holiday', 'winter_break', 'summer_break', 'graduation_summer'):
            begin = day
            names = set()
            end = day
            probe = day
            while True:
                cc = classify_day(probe, cfg)
                if cc['isSchoolDay'] or cc['status'] in ('gaokao', 'gaokao_prep', 'post_graduation'):
                    break
                if cc['status'] != 'weekend':
                    names.add(cc['holiday'] or cc['statusZh'])
                end = probe
                probe += timedelta(days=1)
                if (probe - begin).days > 80:
                    break
            label = '、'.join(sorted(n for n in names if n)) or c['statusZh']
            return {
                'name': label, 'from': begin.isoformat(), 'to': end.isoformat(),
                'days': (end - begin).days + 1, 'startsInDays': (begin - start).days,
            }
        day += timedelta(days=1)
    return None


def _routine_note(c: dict[str, Any]) -> str:
    notes = load_config().get('routine_notes') or DEFAULT_CONFIG['routine_notes']
    if c['status'] == 'gaokao':
        return str(notes.get('gaokao') or '')
    if c['status'] in _SCHOOL_STATUSES:
        return str(notes.get('school') or '')
    # 长假（法定节假日、寒暑假）可以单独写一段；没写时沿用普通休息日说明。
    if c['status'] in ('legal_holiday', 'winter_break', 'summer_break', 'graduation_summer') and notes.get('holiday'):
        return str(notes['holiday'])
    return str(notes.get('rest') or '')


def narrative_context(now: datetime, timezone: str = 'Asia/Shanghai') -> Optional[dict[str, Any]]:
    cfg = load_config()
    if not enabled(cfg):
        return None
    try:
        tz = ZoneInfo(timezone or 'Asia/Shanghai')
    except Exception:  # noqa: BLE001
        tz = ZoneInfo('Asia/Shanghai')
    today = now.astimezone(tz).date()
    t = classify_day(today, cfg)
    gk_start, gk_end = gaokao_range(cfg)
    birth = date.fromisoformat(cfg['birth_date'])
    next_bday = date(today.year, birth.month, birth.day)
    if next_bday < today:
        next_bday = date(today.year + 1, birth.month, birth.day)
    upcoming = []
    for i in range(1, 8):
        d = classify_day(today + timedelta(days=i), cfg)
        upcoming.append({'date': d['date'], 'weekday': d['weekday'], 'statusZh': d['statusZh'],
                         **({'holiday': d['holiday']} if d['holiday'] else {})})
    next_break = _break_runs(today, cfg)
    ctx: dict[str, Any] = {
        'authority': 'real-world calendar for mainland China (official holiday data + high-school calendar rules)',
        'today': t,
        'tomorrow': {k: v for k, v in classify_day(today + timedelta(days=1), cfg).items()
                     if k in ('date', 'weekday', 'status', 'statusZh', 'holiday', 'isSchoolDay')},
        'nextSevenDays': upcoming,
        'grade': t['grade'],
        'schoolPhase': t['phase'],
        'age': age_on(today, cfg),
        'birthday': f'{birth.month}月{birth.day}日',
        'daysToBirthday': (next_bday - today).days,
        'gaokao': {
            'dates': f'{gk_start.isoformat()} ~ {gk_end.isoformat()}',
            'daysLeft': max(0, (gk_start - today).days),
            'finished': today > gk_end,
        },
        'nextBreak': next_break,
        'dailyRoutine': _routine_note(t),
    }
    if t['holidayEstimated'] or any(not classify_day(today + timedelta(days=i), cfg)['officialDataAvailable'] for i in (0, 30)):
        ctx['note'] = '部分日期尚无国务院官方放假安排，节假日为惯例估算、调休补班未知。'
    return ctx


# ============================================================ Schedule Preplan


def _b(block_id: str, start: str, end: str, label: str, kind: str, location: str = '') -> dict[str, Any]:
    block = {'id': block_id, 'start': start, 'end': end, 'label': label, 'kind': kind, 'sourceEntryIds': []}
    if location:
        block['location'] = location
    return block


def _blocks_from_config(key: str, label: str = '', suffix: str = '') -> list[dict[str, Any]]:
    rows = load_config().get(key) or DEFAULT_CONFIG[key]
    blocks = []
    for row in rows:
        try:
            block_id, start, end, text, kind = (str(x) for x in row[:5])
            location = str(row[5]) if len(row) > 5 and row[5] else ''
        except (TypeError, ValueError, IndexError):
            continue
        text = text.replace('{label}', label or '休息日')
        if suffix and block_id.startswith('classes-'):
            text = f'{text}{suffix}'
        blocks.append(_b(block_id, start, end, text, kind, location))
    return blocks or [
        _b(*row[:5], row[5] if len(row) > 5 else '')  # 配置全部无效时回落默认模板
        for row in DEFAULT_CONFIG[key]
    ]


def _school_blocks(label_suffix: str = '') -> list[dict[str, Any]]:
    return _blocks_from_config('school_day_blocks', suffix=label_suffix)


def _rest_blocks(label: str) -> list[dict[str, Any]]:
    return _blocks_from_config('rest_day_blocks', label=label)


def _gaokao_blocks() -> list[dict[str, Any]]:
    return [
        _b('sleep', '00:00', '06:30', '考前睡眠', 'routine', '家'),
        _b('prep', '06:30', '08:00', '起床、吃早饭、赶往考点', 'routine'),
        _b('exam-am', '08:00', '12:00', '高考上午场次', 'fixed', '高考考点'),
        _b('rest-noon', '12:00', '14:30', '午饭休息', 'routine'),
        _b('exam-pm', '14:30', '18:00', '高考下午场次', 'fixed', '高考考点'),
        _b('evening', '18:00', '23:00', '回家休息、准备次日考试', 'flexible', '家'),
    ]


def blocks_for(c: dict[str, Any]) -> list[dict[str, Any]]:
    status = c['status']
    if status in ('school', 'makeup_school'):
        return _school_blocks('（调休补课）' if status == 'makeup_school' else '')
    if status == 'saturday_school':
        return _school_blocks('（周六补课）')
    if status == 'gaokao':
        return _gaokao_blocks()
    label = c['holiday'] or c['statusZh']
    return _rest_blocks(label)


def _template_key(c: dict[str, Any]) -> str:
    s = c['status']
    if s in ('school', 'makeup_school', 'saturday_school'):
        return 'school' + ('-makeup' if s == 'makeup_school' else '-sat' if s == 'saturday_school' else '')
    return 'rest:' + (c['holiday'] or s)


def build_preplan_record(
    story_id: str, today: str, timezone: str, now: datetime,
    current: Optional[dict[str, Any]] = None, materialize: Any = None,
) -> dict[str, Any]:
    """由校历生成完整 Schedule Preplan 记录（写库字段为 snake_case）。

    结构：一条「本学期作息」周规律（周一至周五上课、周六按年级、周日休息），
    视界内与周规律不同的日子写成 `replace` 例外日（法定假日、调休补课、寒暑假、高考等）。
    """
    cfg = load_config()
    horizon = max(3, min(30, int(cfg.get('horizon_days', 14))))
    start = date.fromisoformat(today)
    days = [classify_day(start + timedelta(days=i), cfg) for i in range(horizon)]
    base = classify_day(start, cfg)
    sat_school = bool((cfg.get('saturday_classes') or {}).get(str(max(1, min(grade_of(start, cfg), 3))), False))
    weekly: dict[str, list[dict[str, Any]]] = {}
    for idx, key in enumerate(_WEEKDAY_KEY):
        if idx < 5:
            weekly[key] = _school_blocks()
        elif idx == 5 and sat_school:
            weekly[key] = _school_blocks('（周六补课）')
        else:
            weekly[key] = _rest_blocks('周末')
    regime = {
        'id': 'real-calendar-term', 'label': f'{base["grade"] or "高中"}在校作息（现实校历）',
        'from': today, 'to': (start + timedelta(days=horizon - 1)).isoformat(),
        'weekly': weekly, 'sourceEntryIds': [],
    }

    def regime_key(d: dict[str, Any]) -> str:
        wd = date.fromisoformat(d['date']).weekday()
        if wd < 5:
            return 'school'
        if wd == 5 and sat_school:
            return 'school-sat'
        return 'rest:weekend'

    exceptions = []
    for d in days:
        key = _template_key(d)
        if key == regime_key(d) or (key == 'rest:weekend' and regime_key(d) == 'rest:weekend'):
            continue
        exceptions.append({
            'date': d['date'], 'mode': 'replace',
            'reason': f'现实日历：{d["statusZh"]}' + (f'（{d["holiday"]}）' if d['holiday'] else '')
                      + ('（估算）' if d['holidayEstimated'] else ''),
            'removeBlockIds': [], 'blocks': blocks_for(d), 'sourceEntryIds': [],
        })
    exceptions = exceptions[:30]
    signature = config_signature(cfg)
    summary = '；'.join(f'{d["date"][5:]}{d["statusZh"]}' + (f'·{d["holiday"]}' if d['holiday'] else '')
                        for d in days[:7])
    record = {
        'story_id': story_id,
        'revision': int((current or {}).get('revision') or 0) + 1,
        'timezone': timezone,
        'valid_from': today,
        'valid_through': (start + timedelta(days=horizon - 1)).isoformat(),
        'last_reviewed_local_date': today,
        'last_evidence_entry_id': int((current or {}).get('last_evidence_entry_id')
                                      or (current or {}).get('lastEvidenceEntryId') or 0),
        'review_reason': f'{REASON_TAG} v{VERSION} sig={signature}] {base["grade"]}·{base["phase"]}｜{summary}',
        'regimes': [regime],
        'exceptions': exceptions,
        'materialized_days': materialize([regime], exceptions, today, horizon) if materialize else [],
        'created_at': (current or {}).get('created_at') or (current or {}).get('createdAt') or now,
        'updated_at': now,
    }
    return record


def preplan_is_current(current: Optional[dict[str, Any]], today: str) -> bool:
    """当天已由同一版本/同一配置的现实日历生成过则无需重建。"""
    if not current:
        return False
    reason = str(current.get('review_reason') or current.get('reviewReason') or '')
    last = current.get('last_reviewed_local_date') or current.get('lastReviewedLocalDate')
    return (
        reason.startswith(f'{REASON_TAG} v{VERSION} sig={config_signature(load_config())}]')
        and last == today
    )
