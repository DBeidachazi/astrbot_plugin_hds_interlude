"""现实日历扩展测试：在 AstrBot 容器内以独立进程运行（需要 chinese_calendar / lunarcalendar）。

用法：PYTHONPATH=<插件父目录> HDSI_REAL_CALENDAR_CONFIG=<临时配置> python test_real_calendar.py <插件包名>
"""

import asyncio
import importlib
import json
import os
import sys
import tempfile
from datetime import date, datetime, timedelta, timezone

PKG = sys.argv[1] if len(sys.argv) > 1 else 'hdsi_under_test'
cfg_path = os.path.join(tempfile.mkdtemp(), 'real_calendar.json')
os.environ['HDSI_REAL_CALENDAR_CONFIG'] = cfg_path

rc = importlib.import_module(f'{PKG}.core.real_calendar')
sp = importlib.import_module(f'{PKG}.core.schedule_preplan')
np_ = importlib.import_module(f'{PKG}.core.narrator_prompts')
cc_mod = importlib.import_module(f'{PKG}.core.script.context_compiler')
chunk7 = importlib.import_module(f'{PKG}.core.service.chunk7')

cfg = rc.load_config()
ok = 0


def check(cond, msg):
    global ok
    if not cond:
        raise AssertionError(msg)
    ok += 1


def day(s):
    return rc.classify_day(date.fromisoformat(s), cfg)


# ---------------- T1 国庆 2026（官方数据）----------------
check(day('2026-09-30')['status'] == 'school', day('2026-09-30'))
for d in range(1, 8):
    c = day(f'2026-10-0{d}')
    check(c['status'] == 'legal_holiday' and not c['isSchoolDay'] and not c['holidayEstimated'], c)
check(day('2026-10-01')['holiday'] in ('国庆节', '中秋节'), day('2026-10-01'))
check(day('2026-10-08')['status'] == 'school', day('2026-10-08'))
c = day('2026-10-10')
check(c['status'] == 'makeup_school' and c['isSchoolDay'] and c['holiday'] == '国庆节调休', c)
check(day('2026-10-11')['status'] == 'weekend', day('2026-10-11'))
check(day('2026-10-03')['weekday'] == '星期六', day('2026-10-03'))
print('T1 国庆/调休 ✓')

# ---------------- T2 年级 / 年龄 / 高考 / 毕业 ----------------
check(day('2026-09-29')['grade'] == '高二' and day('2026-09-29')['phase'] == '高二上学期', day('2026-09-29'))
check(rc.age_on(date(2026, 9, 29), cfg) == 17 and rc.age_on(date(2027, 5, 21), cfg) == 18
      and rc.age_on(date(2027, 5, 20), cfg) == 17, 'age')
check(day('2027-03-10')['phase'] == '高二下学期', day('2027-03-10'))
check(day('2027-07-20')['status'] == 'summer_break' and day('2027-07-20')['phase'] == '高二升高三的暑假', day('2027-07-20'))
c = day('2027-08-12')
check(c['grade'] == '准高三' and c['isSchoolDay'], c)
c = day('2027-09-01')
check(c['grade'] == '高三' and c['isSchoolDay'], c)
check(day('2027-09-04')['status'] == 'saturday_school', day('2027-09-04'))       # 高三周六补课
check(day('2026-10-17')['status'] == 'weekend', day('2026-10-17'))              # 高二周六休息
check(day('2028-06-05')['status'] == 'gaokao_prep', day('2028-06-05'))
for d in ('2028-06-07', '2028-06-10'):
    check(day(d)['status'] == 'gaokao' and day(d)['isSchoolDay'], day(d))
check(day('2028-06-11')['status'] == 'graduation_summer' and day('2028-06-11')['grade'] == '高中毕业生', day('2028-06-11'))
check(day('2028-09-05')['status'] == 'post_graduation', day('2028-09-05'))
check(day('2025-08-01')['status'] == 'pre_high_school', day('2025-08-01'))
print('T2 年级/年龄/高考/毕业 ✓')

# ---------------- T3 寒假（农历推算）与无官方数据年份 ----------------
check(rc.spring_festival(2027) == date(2027, 2, 6) and rc.spring_festival(2028) == date(2028, 1, 26), 'cny')
check(day('2027-01-20')['status'] == 'school' and day('2027-01-21')['status'] == 'winter_break', (day('2027-01-20'), day('2027-01-21')))
check(day('2027-02-23')['status'] == 'winter_break' and day('2027-02-24')['status'] == 'school', day('2027-02-24'))
check(day('2028-01-16')['status'] in ('school', 'saturday_school', 'weekend') and day('2028-01-17')['status'] == 'winter_break', day('2028-01-17'))
check(day('2028-02-05')['status'] != 'winter_break', day('2028-02-05'))          # 高三寒假短
c = day('2027-10-01')
check(c['status'] == 'legal_holiday' and c['holidayEstimated'] and c['holiday'] == '国庆节', c)
check(day('2027-05-03')['status'] == 'legal_holiday', day('2027-05-03'))
print('T3 寒假/估算年份 ✓')

# ---------------- T4 Schedule Preplan 记录（经插件自身的归一化/物化/窗口）----------------
tz = 'Asia/Shanghai'
now = datetime(2026, 9, 29, 12, 0, tzinfo=timezone.utc)   # 本地 20:00
rec = rc.build_preplan_record('character:onebot:1690619901', '2026-09-29', tz, now, None, sp.materialize_schedule_preplan)
norm = sp.normalize_schedule_preplan_record(rec)
check(norm is not None and len(norm['regimes']) == 1 and len(norm['materialized_days']) == 14, norm and len(norm['materialized_days']))
exc_dates = [e['date'] for e in norm['exceptions']]
check(exc_dates == [f'2026-10-0{i}' for i in range(1, 8)] + ['2026-10-10'], exc_dates)
days = {d['date']: d for d in norm['materialized_days']}
labels = lambda d: ' '.join(b['label'] for b in days[d]['blocks'])
check('17:00放学' in labels('2026-09-30') and '晚自习' not in labels('2026-09-30') and '课间可以看看手机' in labels('2026-09-30'), labels('2026-09-30'))
check('上午课程' not in labels('2026-10-02') and '国庆' in labels('2026-10-02'), labels('2026-10-02'))
pm = [b for b in days['2026-09-30']['blocks'] if b['id'] == 'classes-pm'][0]
check(pm['end'] == '17:00' and pm['kind'] == 'fixed', pm)
check('调休补课' in labels('2026-10-10') and '下午课程' in labels('2026-10-10'), labels('2026-10-10'))
check('课程' not in labels('2026-10-11'), labels('2026-10-11'))
check(norm['review_reason'].startswith(f'[real-calendar v{rc.VERSION} sig='), norm['review_reason'])
check(rc.preplan_is_current(norm, '2026-09-29') and not rc.preplan_is_current(norm, '2026-09-30'), 'is_current')
win = sp.schedule_preplan_window(norm, datetime(2026, 9, 30, 12, 0, tzinfo=timezone.utc), tz, 12)  # 本地 9/30 20:00
check(win and any('自由时间' in b['label'] for b in win['blocks']) and not any('课程' in b['label'] for b in win['blocks'] if b['date'] == '2026-09-30'), win)
win2 = sp.schedule_preplan_window(norm, datetime(2026, 10, 1, 2, 0, tzinfo=timezone.utc), tz, 12)  # 本地 10/1 10:00
check(win2 and all('课' not in b['label'] for b in win2['blocks']), win2)
nxt = sp.next_schedule_preplan_transition(norm, datetime(2026, 9, 29, 22, 30, tzinfo=timezone.utc), tz, 12)  # 本地 9/30 06:30
check(nxt is not None, nxt)
print('T4 Preplan 记录/物化/窗口 ✓', f'例外日={len(exc_dates)}', f'下一固定块={nxt}')

# ---------------- T5 主叙事上下文 ----------------
ctx = rc.narrative_context(now, tz)
check(ctx['today']['status'] == 'school' and ctx['grade'] == '高二' and ctx['age'] == 17, ctx['today'])
check('17:00放学' in ctx['dailyRoutine'] and '课间' in ctx['dailyRoutine'], ctx['dailyRoutine'])
hol = rc.narrative_context(datetime(2026, 10, 2, 4, 0, tzinfo=timezone.utc), tz)
check(hol['dailyRoutine'].startswith('长假'), hol['dailyRoutine'])  # 国庆走长假专用说明
wkd = rc.narrative_context(datetime(2026, 10, 18, 4, 0, tzinfo=timezone.utc), tz)  # 10/18 周日
check(wkd['dailyRoutine'].startswith('休息日'), wkd['dailyRoutine'])
nb = ctx['nextBreak']
check(nb['from'] == '2026-10-01' and nb['to'] == '2026-10-07' and nb['startsInDays'] == 2 and '国庆节' in nb['name'], nb)
check(ctx['gaokao']['dates'].startswith('2028-06-07') and ctx['gaokao']['daysLeft'] == (date(2028, 6, 7) - date(2026, 9, 29)).days, ctx['gaokao'])
check(len(ctx['nextSevenDays']) == 7 and ctx['nextSevenDays'][1]['statusZh'].startswith('法定'), ctx['nextSevenDays'][:2])
field = np_._real_calendar_field(now.isoformat(), tz)
check(field.get('realCalendar', {}).get('grade') == '高二', field)
compiled = cc_mod.compile_narrative_context({'interval': {'nowLocal': 'x', **field}, 'phase': 'advance'}, None, None)
check(compiled['authoringWindow']['interval']['realCalendar']['today']['date'] == '2026-09-29', 'compiled drop')
local = np_._local_compile_narrative_context({'interval': {'nowLocal': 'x', **field}, 'phase': 'advance', 'recentScript': []}, None, None)
check(local['authoringWindow']['interval']['realCalendar']['grade'] == '高二', 'local compile drop')
check(np_._real_calendar_field('not-a-date', tz) == {}, 'bad date must not raise')
sysp = np_.system_prompt('advance', None, None, '', '', '')
check('interval.realCalendar' in sysp and 'dailyRoutine' in sysp, 'rule missing from system prompt')
print('T5 叙事上下文/编译保留/提示词规则 ✓')

# ---------------- T6 服务钩子 + 游标修复 ----------------
class FakeService:
    schedule_preplan_config = sp.resolve_schedule_preplan_config({})
    shared_story_config = {'shareParticipantDetails': True}

    def __init__(self, rows, current=None):
        self.rows = rows
        self.table = {'character:onebot:1690619901': current} if current else {}
        self.schedule_preplan_backoff = {}
        self.ops = []

    async def db_get(self, table, where, options=None):
        if table == 'interlude_schedule_preplan':
            r = self.table.get(where['storyId'])
            return [r] if r else []
        rows = sorted(self.rows, key=lambda r: r['occurredAt'])
        return rows[: options['limit']] if options and options.get('limit') else rows

    async def db_set(self, table, where, update):
        self.table[where['storyId']] = {**self.table[where['storyId']], **update}

    async def db_create(self, table, payload):
        self.table[payload['storyId']] = payload

    def report_operation(self, *a):
        self.ops.append(a)

    def report(self, *a):
        self.ops.append(a)


for name in ('get_schedule_preplan', 'save_schedule_preplan', 'prepare_schedule_preplan_review', 'schedule_preplan_evidence'):
    setattr(FakeService, name, getattr(chunk7.ServiceChunk7, name))

story = {'id': 'character:onebot:1690619901', 'setting': {'timezone': tz}, 'state': {}}
old = {'storyId': story['id'], 'revision': 1, 'timezone': tz, 'validFrom': '2026-09-29', 'validThrough': '2026-10-12',
       'lastReviewedLocalDate': '2026-09-29', 'lastEvidenceEntryId': 421, 'reviewReason': 'sleeping', 'regimes': [],
       'exceptions': [], 'materializedDays': []}
svc = FakeService([], dict(old))


async def run_hook():
    r1 = await svc.prepare_schedule_preplan_review(story, now)
    check(r1 and r1['needs_model'] is False and r1['request'] is None, r1)
    stored = await svc.get_schedule_preplan(story['id'])
    check(stored['revision'] == 2 and len(stored['exceptions']) == 8 and stored['review_reason'].startswith('[real-calendar'), stored['revision'])
    check(stored['last_evidence_entry_id'] == 421, 'cursor preserved for fallback')
    r2 = await svc.prepare_schedule_preplan_review(story, now + timedelta(hours=2))
    check(r2 is None, 'same day must not rebuild')
    r3 = await svc.prepare_schedule_preplan_review(story, now + timedelta(days=1))
    stored3 = await svc.get_schedule_preplan(story['id'])
    check(r3 and stored3['valid_from'] == '2026-09-30' and stored3['revision'] == 3, stored3['valid_from'])
    # 配置改变 → 当天重建；关闭 → 回到原模型流程
    json.dump({'saturday_classes': {'2': True}, 'routine_notes': {'school': '测试说明'},
               'school_day_blocks': [['classes-am', '08:00', '12:00', '测试上午课', 'fixed', '学校'], ['bad']]}, open(cfg_path, 'w'))
    check(rc.narrative_context(now, tz)['dailyRoutine'] == '测试说明', 'routine note hot reload')
    sb = rc._school_blocks('（周六补课）')
    check(len(sb) == 1 and sb[0]['label'] == '测试上午课（周六补课）' and sb[0]['start'] == '08:00', sb)
    r4 = await svc.prepare_schedule_preplan_review(story, now + timedelta(days=1, hours=1))
    check(r4 is not None, 'config change must rebuild')
    json.dump({'enabled': False}, open(cfg_path, 'w'))
    r5 = await svc.prepare_schedule_preplan_review(story, now + timedelta(days=1, hours=2))
    check(r5 is None or r5.get('needs_model') is not None, r5)   # 原流程：当天已复核 → None
    os.remove(cfg_path)
    # 游标修复：游标之后 200 条，应取最新 60 条
    rows = [{'id': i, 'kind': 'script', 'participantId': '', 'occurredAt': f'2026-09-28T{i // 60:02d}:{i % 60:02d}:00Z',
             'content': 'x', 'metadata': {}} for i in range(1, 400)]
    svc2 = FakeService(rows)
    svc2.shared_story_config = {'shareParticipantDetails': True}
    ev = await svc2.schedule_preplan_evidence(story['id'], 199)
    check(len(ev) == 60 and ev[0]['id'] == 340 and ev[-1]['id'] == 399, (ev[0]['id'], ev[-1]['id']))


asyncio.run(run_hook())
print('T6 服务钩子/每日重建/配置变更/关闭回退/游标修复 ✓')
print(f'\n【现实日历全部测试通过】断言 {ok} 项')
