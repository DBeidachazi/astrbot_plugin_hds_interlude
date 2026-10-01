"""生活活力（防停滞 / 睡眠合并 / 生活钩子 / 长线剧情 / 提示词）测试。

用法：PYTHONPATH=<插件父目录> python test_vitality.py <插件包名>
宿主机没有 chinese_calendar 时日历相关函数会退回「周末=休息日」的粗分，用例按函数本身的
返回值计算期望，两种环境都能跑。
"""

import importlib
import json
import os
import sys
import tempfile
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

PKG = sys.argv[1] if len(sys.argv) > 1 else 'hds_interlude'
tmp = tempfile.mkdtemp()
# 指向不存在的文件 → 使用内置默认库，不受部署目录里配置的影响。
os.environ['HDSI_LIFE_HOOKS_CONFIG'] = os.path.join(tmp, 'life_hooks.json')
os.environ['HDSI_STORY_ARCS_CONFIG'] = os.path.join(tmp, 'story_arcs.json')

lh = importlib.import_module(f'{PKG}.core.life_hooks')
sa = importlib.import_module(f'{PKG}.core.story_arcs')
vt = importlib.import_module(f'{PKG}.core.vitality')
np_ = importlib.import_module(f'{PKG}.core.narrator_prompts')
cc_mod = importlib.import_module(f'{PKG}.core.script.context_compiler')

TZ = 'Asia/Shanghai'
ZONE = ZoneInfo(TZ)
ok = 0


def check(cond, msg):
    global ok
    if not cond:
        raise AssertionError(msg)
    ok += 1


def local(y, m, d, hh, mm=0):
    return datetime(y, m, d, hh, mm, tzinfo=ZONE).astimezone(timezone.utc)


# ============================================================ life_hooks
day = date(2026, 10, 3)
plan = lh.daily_plan('s1', day, 'holiday', TZ)
check(plan == lh.daily_plan('s1', day, 'holiday', TZ), '同一天计划必须确定')
check(1 <= sum(1 for item in plan if item['tier'] == 'minor') <= 2, plan)
check(all(item['id'].endswith('#2026-10-03') and item['event'] and '{' not in item['event'] for item in plan), plan)
check(all(item['revealAt'].tzinfo is not None for item in plan), 'revealAt 带时区')
hooks_by_id = {hook['id']: hook for hook in lh.DEFAULT_CONFIG['hooks']}
for item in plan:
    hook = hooks_by_id[item['hook']]
    check(lh.category_matches(hook.get('days'), 'holiday'), f'{item["hook"]} 不该出现在假期')
    hours = hook.get('hours') or lh.DEFAULT_CONFIG['default_hours']['off']
    hour = item['revealAt'].astimezone(ZONE).hour
    check(hours[0] <= hour < hours[1], f'{item["hook"]} 揭晓时间越界 {hour}')
# 上课日只出上课日钩子
for offset in range(20):
    for item in lh.daily_plan('s1', date(2026, 10, 12) + timedelta(days=offset), 'school', TZ):
        check(lh.category_matches(hooks_by_id[item['hook']].get('days'), 'school'), item)
check(lh.category_matches(['off'], 'break') and not lh.category_matches(['off'], 'school'), 'off 别名')
check(lh.daily_plan('s1', day, 'exam', TZ) == [] or all(
    lh.category_matches(hooks_by_id[i['hook']].get('days'), 'exam') for i in lh.daily_plan('s1', day, 'exam', TZ)), 'exam')

# 揭晓前不交付；交付后不重复；交付后当天计划不变
if plan:
    first = plan[0]
    before = lh.due_hooks('s1', first['revealAt'] - timedelta(minutes=1), 'holiday', TZ, {})
    check(first['id'] not in [item['id'] for item in before], '揭晓前不应交付')
    due = lh.due_hooks('s1', first['revealAt'], 'holiday', TZ, {})
    check(due and due[0]['id'] == first['id'], due)
    history = lh.record_outcomes({}, due, [{'id': first['id'], 'outcome': 'taken', 'note': '去了'}],
                                 first['revealAt'], TZ)
    check(history[first['id']]['outcome'] == 'taken' and history[first['id']]['note'] == '去了', history)
    check(lh.daily_plan('s1', day, 'holiday', TZ, history) == plan, '交付后当天计划必须不变')
    check(first['id'] not in [i['id'] for i in lh.due_hooks('s1', local(2026, 10, 3, 23), 'holiday', TZ, history)],
          '已交付不重复')
    # 冷却：同一钩子次日（冷却≥2天）不再出现
    cool = hooks_by_id[first['hook']].get('cooldown_days', 1)
    if cool >= 2:
        nxt = lh.daily_plan('s1', day + timedelta(days=1), 'holiday', TZ, history)
        check(first['hook'] not in [i['hook'] for i in nxt], '冷却期内不应重复')
# 漏报 → unreported；回报未交付的 id 被忽略；过期记录被剪掉
fake = [{'id': 'errand#2026-10-03', 'hook': 'errand', 'tier': 'minor', 'event': '买酱油', 'arc': None}]
rec = lh.record_outcomes({'old#2026-08-01': {'hook': 'old', 'date': '2026-08-01'}}, fake,
                         [{'id': 'ghost#x', 'outcome': 'taken'}], local(2026, 10, 3, 12), TZ)
check(rec['errand#2026-10-03']['outcome'] == 'unreported' and 'ghost#x' not in rec and 'old#2026-08-01' not in rec, rec)
# 每周中等事件预算：前 3 天已有 medium → 30 个日子里都不该再出 medium
hist = {'guitar-club#2026-10-01': {'hook': 'guitar-club', 'tier': 'medium', 'date': '2026-10-01'}}
for offset in range(1, 6):
    plan_d = lh.daily_plan('s2', date(2026, 10, 1) + timedelta(days=offset), 'holiday', TZ, hist)
    check(all(item['tier'] != 'medium' for item in plan_d), '每周 medium 预算')
# 长期看 medium 会出现（概率>0）
check(any(item['tier'] == 'medium' for offset in range(120)
          for item in lh.daily_plan('s3', date(2026, 1, 1) + timedelta(days=offset), 'holiday', TZ)), 'medium 从不出现')
# 配置热读取 + disabled
json.dump({'enabled': False}, open(os.environ['HDSI_LIFE_HOOKS_CONFIG'], 'w'))
check(lh.daily_plan('s1', day, 'holiday', TZ) == [], 'disabled')
os.remove(os.environ['HDSI_LIFE_HOOKS_CONFIG'])
check(lh.daily_plan('s1', day, 'holiday', TZ) == plan, '删除配置后回到默认库')
print('T1 life_hooks ✓')

# ============================================================ story_arcs
d0 = date(2026, 10, 3)
arcs, started = sa.start_arc({}, 'guitar-f-chord', d0, 'test')
check(started and arcs['guitar-f-chord']['stage'] == 0, arcs)
arcs2, again = sa.start_arc(arcs, 'guitar-f-chord', d0, 'test')
check(not again, '不能重复开启')
arcs, _ = sa.start_arc(arcs, 'stray-cat', d0, 'test')
arcs3, third = sa.start_arc(arcs, 'lost-notebook', d0, 'test')
check(not third and sa.active_count(arcs3) == 2, '并发上限 2')
check(not sa.start_arc({}, 'no-such-arc', d0, 'x')[1], '未定义主线')
ctx = sa.context(arcs, d0)
guitar = next(item for item in ctx['activeArcs'] if item['id'] == 'guitar-f-chord')
check(guitar['stageIndex'] == 1 and guitar['nextBeat'] and not guitar['canAdvance'], guitar)
script = '她咬着牙又按了一遍横按，江晚学姐看不下去，捏着她的手指教了个小窍门。'
quote = '江晚学姐看不下去，捏着她的手指教了个小窍门'
same_day, changes = sa.apply_progress(arcs, [{'id': 'guitar-f-chord', 'quote': quote}], script, d0)
check(not changes, '未满最短天数不推进')
d2 = d0 + timedelta(days=2)
check(next(i for i in sa.context(arcs, d2)['activeArcs'] if i['id'] == 'guitar-f-chord')['canAdvance'], 'canAdvance')
bad, changes = sa.apply_progress(arcs, [{'id': 'guitar-f-chord', 'quote': '剧本里没有的话'}], script, d2)
check(not changes, 'quote 不在剧本里不推进')
arcs, changes = sa.apply_progress(arcs, [{'id': 'guitar-f-chord', 'quote': quote}], script, d2)
check(changes and changes[0]['change'] == 'advanced' and arcs['guitar-f-chord']['stage'] == 1, changes)
arcs, changes = sa.apply_progress(arcs, [{'id': 'guitar-f-chord', 'quote': quote}], script, d2)
check(not changes, '同一天只推进一步')
# 一路推进到完成
cursor = d2
stages = len(next(a for a in sa.DEFAULT_CONFIG['arcs'] if a['id'] == 'guitar-f-chord')['stages'])
for _ in range(stages):
    cursor += timedelta(days=2)
    arcs, changes = sa.apply_progress(arcs, [{'id': 'guitar-f-chord', 'quote': quote}], script, cursor)
check(arcs['guitar-f-chord']['status'] == 'completed', arcs['guitar-f-chord'])
ctx = sa.context(arcs, cursor)
check('F和弦与文艺汇演' in ctx.get('pastArcs', []) and all(i['id'] != 'guitar-f-chord' for i in ctx['activeArcs']), ctx)
# 放下一条线
arcs, changes = sa.apply_progress(arcs, [{'id': 'stray-cat', 'outcome': 'dropped', 'quote': quote}], script, cursor)
check(arcs['stray-cat']['status'] == 'dropped', arcs['stray-cat'])
# 自发开启：确定性，且只在没有活跃主线时
picks = [sa.maybe_spontaneous('s1', {}, date(2026, 1, 1) + timedelta(days=i), 'rest') for i in range(60)]
check(picks == [sa.maybe_spontaneous('s1', {}, date(2026, 1, 1) + timedelta(days=i), 'rest') for i in range(60)], '确定性')
check(any(picks) and not all(picks), picks)
check(all(p in ('guitar-f-chord', None) for p in picks), '月考线只在上课日自发')
active_state, _ = sa.start_arc({}, 'stray-cat', d0, 'x')
check(all(sa.maybe_spontaneous('s1', active_state, date(2026, 1, 1) + timedelta(days=i), 'rest') is None for i in range(60)),
      '有活跃主线时不自发')
print('T2 story_arcs ✓')

# ============================================================ vitality：睡眠 / 停滞


def entry(at, place, activity, kind='script'):
    return {'kind': kind, 'occurredAt': at.isoformat().replace('+00:00', 'Z'),
            'metadata': json.dumps({'life_handoff': {'place': {'value': place}, 'activity': {'value': activity}}},
                                   ensure_ascii=False)}


check(vt.is_asleep('在温暖被窝中平稳熟睡') and vt.is_asleep('长假首日早晨惬意赖床回笼觉'), 'asleep')
check(not vt.is_asleep('从被窝爬起来回复群聊') and not vt.is_asleep('睡醒了伸懒腰起床'), 'awake')
check(vt.place_key('家里自己卧室床上') == vt.place_key('家里自己的卧室书桌前') == 'bedroom', 'place key')
check(vt.place_key('家里餐厅餐桌旁') == 'kitchen' and vt.place_key('学校教室') == 'school', 'place key 2')

# 睡眠合并：23:30 睡着 → 次日睡眠块结束；凌晨 03:00 睡着 → 当天睡眠块结束；白天午睡 → 不合并
night = [entry(local(2026, 10, 3, 23, 20), '家里自己的卧室床上', '关灯钻进被窝准备入睡')]
resume = vt.sleep_resume_at(night, local(2026, 10, 3, 23, 30), TZ)
nxt = date(2026, 10, 4)
check(resume == datetime.combine(nxt, vt.sleep_block_end(nxt), tzinfo=ZONE).astimezone(timezone.utc), resume)
deep = [entry(local(2026, 10, 4, 2, 50), '家里自己的卧室床上', '在被窝里平稳熟睡')]
resume = vt.sleep_resume_at(deep, local(2026, 10, 4, 3, 0), TZ)
check(resume == datetime.combine(nxt, vt.sleep_block_end(nxt), tzinfo=ZONE).astimezone(timezone.utc), resume)
nap = [entry(local(2026, 10, 4, 14, 0), '家里客厅沙发', '窝在沙发上睡着了')]
check(vt.sleep_resume_at(nap, local(2026, 10, 4, 14, 10), TZ) is None, '午睡不合并')
check(vt.sleep_resume_at([entry(local(2026, 10, 3, 23, 20), '卧室', '在床上刷手机')], local(2026, 10, 3, 23, 30), TZ)
      is None, '醒着不合并')
check(vt.sleep_resume_at([], local(2026, 10, 3, 23, 30), TZ) is None, '无条目')

# 停滞：休息日 10:00 起一直在卧室 → 14:00 触发；中途换过地点 → 不触发；睡着 → 不触发；上课时间 → 不触发
bed = [entry(local(2026, 10, 3, 10 + i // 2, 30 * (i % 2)), '家里自己卧室床上', '趴在床上搂着煤球水群') for i in range(8)]
stag = vt.life_stagnation(bed, local(2026, 10, 3, 14, 0), TZ, 'holiday')
check(stag and stag['sinceLocal'] == '10:00' and stag['hours'] == 4.0, stag)
moved = bed[:5] + [entry(local(2026, 10, 3, 12, 40), '家里餐厅餐桌旁', '吃午饭')] + bed[5:]
moved.sort(key=lambda e: e['occurredAt'])
check(vt.life_stagnation(moved, local(2026, 10, 3, 14, 0), TZ, 'holiday') is None, '换过地点不触发')
check(vt.life_stagnation(bed, local(2026, 10, 3, 12, 0), TZ, 'holiday') is None, '不足 3 小时')
sleepy = bed + [entry(local(2026, 10, 3, 13, 50), '家里自己卧室床上', '搂着煤球沉沉入睡')]
check(vt.life_stagnation(sleepy, local(2026, 10, 3, 14, 0), TZ, 'holiday') is None, '睡着不触发')
school = [entry(local(2026, 10, 12, 8 + i, 0), '学校教室', '上课') for i in range(6)]
check(vt.life_stagnation(school, local(2026, 10, 12, 14, 0), TZ, 'school') is None, '上课时间不触发')
check(vt.life_stagnation(bed, local(2026, 10, 3, 14, 0), TZ, 'exam') is None, '考试期间不触发')
# 非 script 条目与缺 handoff 的条目被忽略
noise = bed + [{'kind': 'group-message', 'occurredAt': '2026-10-03T05:59:00Z', 'metadata': {}}]
check(vt.life_stagnation(noise, local(2026, 10, 3, 14, 0), TZ, 'holiday') == stag, '噪声条目')
print('T3 vitality 睡眠/停滞 ✓')

# ============================================================ vitality：请求与落库编排
story = {'id': 's1', 'setting': {'timezone': TZ}}
cat = vt.calendar_category(day)
plan_now = lh.daily_plan('s1', day, cat, TZ)
if plan_now:
    at = plan_now[-1]['revealAt'] + timedelta(minutes=1)
    ctx = vt.request_context(story, {}, 'advance', at, [])
    check(ctx.get('lifeHooks') and all('id' in h and 'event' in h for h in ctx['lifeHooks']), ctx)
    check(not vt.request_context(story, {}, 'user-message', at, []).get('lifeHooks'), '回复回合不注入钩子')
    raw = {'hookOutcomes': [{'id': ctx['lifeHooks'][0]['id'], 'outcome': 'declined', 'note': '懒得动'}]}
    state, logs = vt.record_turn(story, {}, 'advance', at, raw, '她看了一眼消息。')
    check(state and state['hooks'][ctx['lifeHooks'][0]['id']]['outcome'] == 'declined', state)
    check(any('生活钩子' in line for line in logs), logs)
    next_ctx = vt.request_context(story, {'extensions': {'vitality': state}}, 'advance', at, [])
    check(ctx['lifeHooks'][0]['id'] not in [h['id'] for h in next_ctx.get('lifeHooks') or []], '已交付不再注入')
    # user-message 回合不记录钩子
    state2, _ = vt.record_turn(story, {}, 'user-message', at, raw, '她看了一眼消息。')
    check(not (state2 or {}).get('hooks'), '回复回合不记钩子')
# 带主线的钩子被接受 → 开启主线
arc_story = {'id': 'arc-story', 'setting': {'timezone': TZ}}
found = None
for offset in range(400):
    d = date(2026, 1, 1) + timedelta(days=offset)
    for item in lh.daily_plan('arc-story', d, vt.calendar_category(d), TZ):
        if item['arc']:
            found = item
            break
    if found:
        break
check(found is not None, '找不到带主线的钩子')
at = found['revealAt'] + timedelta(minutes=1)
# 同一时刻到期的其它钩子先当作已交付（每回合最多交付 MAX_PER_TURN 个）
earlier = {item['id']: {'hook': item['hook'], 'tier': item['tier'], 'date': str(found['revealAt'].astimezone(ZONE).date())}
           for item in lh.daily_plan('arc-story', found['revealAt'].astimezone(ZONE).date(),
                                     vt.calendar_category(found['revealAt'].astimezone(ZONE).date()), TZ)
           if item['id'] != found['id'] and item['revealAt'] <= found['revealAt']}
base = {'extensions': {'vitality': {'hooks': earlier}}}
due_ids = [h['id'] for h in vt.turn_hooks(arc_story, base, 'advance', at)]
check(found['id'] in due_ids, due_ids)
state, logs = vt.record_turn(arc_story, base, 'advance', at, {'hookOutcomes': [{'id': found['id'], 'outcome': 'taken'}]}, 'x')
check(state['arcs'][found['arc']]['status'] == 'active' and any('长线剧情开启' in line for line in logs), (state, logs))
state_d, _ = vt.record_turn(arc_story, base, 'advance', at, {'hookOutcomes': [{'id': found['id'], 'outcome': 'declined'}]}, 'x')
check(found['arc'] not in (state_d or {}).get('arcs', {}) or state_d['arcs'][found['arc']].get('reason') == 'spontaneous',
      '拒绝的钩子不开启主线')
# 坏输入不抛异常
check(vt.request_context(None, None, 'advance', at, None) == {} or isinstance(vt.request_context(None, None, 'advance', at, None), dict), 'robust')
check(isinstance(vt.record_turn(None, 'bad', 'advance', at, None, None), tuple), 'robust record')
print('T4 vitality 编排 ✓')

# ============================================================ 提示词与上下文管道
sysp = np_.system_prompt('advance', None, None, '', '', '')
check('not an obligation' in sysp and 'follow it even where' not in sysp, '作息降级')
check('interval.lifeStagnation' in sysp and 'interval.lifeHooks' in sysp and 'hookOutcomes' in sysp, '钩子/停滞规则')
check('interval.activeArcs' in sysp and 'arcProgress' in sysp, '主线规则')
check('online acquaintance in person never becomes a plan' in sysp, '安全边界')
check('scheduled intent with a plausible notBefore' in sysp, '聊天转计划')
fields = np_._vitality_fields({'vitality': {'lifeHooks': [{'id': 'a', 'event': 'b'}], 'activeArcs': [], 'junk': 1}})
check(fields == {'lifeHooks': [{'id': 'a', 'event': 'b'}]}, fields)
check(np_._vitality_fields({}) == {} and np_._vitality_fields({'vitality': 'bad'}) == {}, 'empty')
interval = {'nowLocal': 'x', 'lifeStagnation': {'place': '卧室', 'hours': 3.5}, 'lifeHooks': [{'id': 'a', 'event': 'b'}],
            'activeArcs': [{'id': 'g', 'title': 't'}], 'pastArcs': ['p']}
compiled = cc_mod.compile_narrative_context({'interval': interval, 'phase': 'advance'}, None, None)
kept = compiled['authoringWindow']['interval']
check(all(key in kept for key in ('lifeStagnation', 'lifeHooks', 'activeArcs', 'pastArcs')), f'编译丢字段 {list(kept)}')
local_compiled = np_._local_compile_narrative_context({'interval': interval, 'phase': 'advance', 'recentScript': []}, None, None)
check('lifeHooks' in local_compiled['authoringWindow']['interval'], '本地编译丢字段')
print('T5 提示词/上下文管道 ✓')

# ============================================================ 服务层：睡眠合并接入 chunk7 调度
import asyncio  # noqa: E402

chunk7 = importlib.import_module(f'{PKG}.core.service.chunk7')


class FakeService:
    auto_advance_config = {'enabled': True, 'interval_minutes': 40, 'jitter_minutes': 0, 'rest_windows': [],
                           'follow_up_minutes': [10, 20]}
    runtime_config = {}
    config = {}

    def __init__(self, entries):
        self.entries = entries
        self.story = {'id': 'svc', 'setting': {'timezone': TZ}, 'state': {}}
        self.ops = []

    async def get_story(self, story_id):
        return self.story

    async def recent_entries(self, story_id, limit=None):
        return self.entries[-(limit or 20):]

    async def db_set(self, table, where, update):
        self.story = {**self.story, **update}

    async def schedule_preplan_anchored_time(self, story, now, ordinary_next):
        return ordinary_next

    def report_operation(self, *args):
        self.ops.append(args)


for name in ('sleep_resume_at', 'schedule_conversation_follow_ups_after_turn', 'schedule_next_automatic_advance'):
    setattr(FakeService, name, getattr(chunk7.ServiceChunk7, name))


def automation(service):
    state = service.story['state']
    return state.get('automation') or {}


async def service_cases():
    at = local(2026, 10, 3, 23, 30)
    expected = datetime.combine(date(2026, 10, 4), vt.sleep_block_end(date(2026, 10, 4)), tzinfo=ZONE).astimezone(timezone.utc)
    asleep = FakeService([entry(local(2026, 10, 3, 23, 20), '家里自己的卧室床上', '关灯钻进被窝准备入睡')])
    await asleep.schedule_conversation_follow_ups_after_turn('svc', at)
    auto = automation(asleep)
    check(auto.get('conversation_follow_up_at') == [] and vt._parse(auto.get('next_advance_at')) == expected, auto)
    awake = FakeService([entry(local(2026, 10, 3, 21, 0), '家里自己的卧室书桌前', '在书桌前练吉他')])
    await awake.schedule_conversation_follow_ups_after_turn('svc', local(2026, 10, 3, 21, 5))
    check(len(automation(awake).get('conversation_follow_up_at') or []) == 2, automation(awake))
    regular = FakeService([entry(local(2026, 10, 3, 23, 20), '家里自己的卧室床上', '在被窝里平稳熟睡')])
    await regular.schedule_next_automatic_advance('svc', at)
    check(vt._parse(automation(regular).get('next_advance_at')) == expected, automation(regular))
    check(any('睡眠合并' in str(op) for op in regular.ops), regular.ops)


asyncio.run(service_cases())
print('T6 服务层睡眠合并 ✓')

print(f'test_vitality: {ok} checks passed')
