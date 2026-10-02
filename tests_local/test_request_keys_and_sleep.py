"""请求键名兼容（camelCase ⇄ snake_case）与夜间免打扰测试。

用法：PYTHONPATH=<插件父目录> python test_request_keys_and_sleep.py <插件包名>
"""

import asyncio
import importlib
import json
import sys
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

PKG = sys.argv[1] if len(sys.argv) > 1 else 'hds_interlude'
nm = importlib.import_module(f'{PKG}.core.narrator')
np_ = importlib.import_module(f'{PKG}.core.narrator_prompts')
vt = importlib.import_module(f'{PKG}.core.vitality')
chunk1 = importlib.import_module(f'{PKG}.core.service.chunk1')
chunk7 = importlib.import_module(f'{PKG}.core.service.chunk7')
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


# ============================================================ T1 normalize_request_keys
src = {'alterEnabled': True, 'agency_enabled': True, 'groupContext': {'groupId': '1'}, 'outputRecovery': False,
       'schedulePreplan': {'blocks': []}, 'writing_options': {'messageSeparator': '<sep/>'}, 'phase': 'advance'}
out = nm.normalize_request_keys(src)
check(out['alter_enabled'] is True and out['alterEnabled'] is True, 'camel → snake')
check(out['agencyEnabled'] is True, 'snake → camel')
check(out['group_context'] == {'groupId': '1'} and out['schedule_preplan'] == {'blocks': []}, 'dict 值')
check(out['output_recovery'] is False, 'False 也要传过去')
check(out['writingOptions'] == {'messageSeparator': '<sep/>'}, 'writing options')
check('alter_enabled' not in src, '不修改原请求')
check(nm.normalize_request_keys(out) == out, '幂等')
both = nm.normalize_request_keys({'alterEnabled': False, 'alter_enabled': True})
check(both['alter_enabled'] is True and both['alterEnabled'] is False, '已有值不覆盖')
check(nm.normalize_request_keys(None) is None and nm.normalize_request_keys('x') == 'x', '非 dict 原样返回')
print('T1 键名兼容 ✓')

# ============================================================ T2 系统提示词实际拿到开关（真实 narrator，桩掉 HTTP）
captured = {}
original = nm.system_prompt


def spy(*args, **kwargs):
    captured['args'] = args
    captured['text'] = original(*args, **kwargs)
    return captured['text']


nm.system_prompt = spy


class Stop(Exception):
    pass


class FakeHttp:
    async def post_json(self, *args, **kwargs):
        raise Stop()

    async def iterate_sse(self, *args, **kwargs):
        raise Stop()


narrator = nm.OpenAICompatibleNarrator(FakeHttp(), {}, True)
provider = {'name': 'x', 'endpoint': 'http://x', 'model': 'm'}
now = datetime(2026, 10, 2, 6, 0, tzinfo=timezone.utc)


def base_request(**extra):
    request = {'phase': 'user-message', 'story': {'id': 's', 'setting': {'timezone': TZ}, 'state': {}},
               'from': now - timedelta(minutes=5), 'now': now, 'recentEntries': [], 'participants': [],
               'alterEnabled': True, 'agencyEnabled': True, 'schedulePreplan': {'blocks': [{'id': 'b'}]},
               'writingOptions': {'messageSeparator': '<sep/>', 'splitReplyMessages': True}}
    request.update(extra)
    return request


def prompt_for(request, via_decide=False):
    captured.clear()
    try:
        if via_decide:
            asyncio.run(narrator.decide(request))
        else:
            asyncio.run(narrator._request_provider(provider, request))
    except Exception:  # noqa: BLE001 - 桩 HTTP 一定抛
        pass
    return captured.get('args'), captured.get('text') or ''


args, text = prompt_for(base_request(groupContext={'groupId': 'g', 'messages': []}))
check(args is not None, 'system_prompt 未被调用')
check(args[7] is True and args[8] is True and args[14] is True and args[17] is True, args[7:18])
check('For this group turn, return groupReply' in text, '群聊应拿到 groupReply 传输说明')
check('For this private turn' not in text, '群聊不应拿到私聊传输说明')
check('integer field named alter' in text, '情绪（Alter）规则')
# 合并 KelaLeaf 后：默认特化档（off ≡ full）仍带本地扩展规则
for rule in ('interval.realCalendar', 'interval.lifeStagnation', 'interval.lifeHooks', 'interval.activeArcs',
             'interval.overnightMessages', 'online acquaintance in person never becomes a plan'):
    check(rule in text, '默认档提示词缺少本地扩展规则 %s' % rule)
check('agencyWindow may be' not in text, '按上游设计，回复回合不带行动窗口规则')
check('Schedule Preplan contains only the coming roughly twelve hours' in text, '日程用法规则')
args, text = prompt_for(base_request(phase='advance'))
check('agencyWindow may be' in text and 'integer field named alter' in text, '自主回合：行动窗口 + 情绪规则')
args, text = prompt_for(base_request(outputRecovery=True, groupContext={'groupId': 'g', 'messages': []}))
check('OUTPUT RECOVERY' in text, '重写纠错规则')
args, text = prompt_for(base_request())
check(args[17] is False and 'For this private turn' in text and 'For this group turn' not in text, '私聊回合仍是私聊说明')
args, text = prompt_for(base_request(phase='advance', alterEnabled=False, agencyEnabled=False))
check('integer field named alter' not in text and 'agencyWindow may be' not in text, '关掉时不注入')
args, text = prompt_for(base_request(alter_enabled=True, group_context={'groupId': 'g', 'messages': []}))
check(args[7] is True and args[17] is True, 'snake_case 请求同样有效')
# decide() 入口（含 provider 选择）也走归一化
nm.OpenAICompatibleNarrator._assigned_providers = lambda self, task: [provider]
args, text = prompt_for(base_request(groupContext={'groupId': 'g', 'messages': []}), via_decide=True)
check(args is not None and args[7] is True and args[17] is True, 'decide() 入口')
nm.system_prompt = original
print('T2 系统提示词开关 ✓')

# ============================================================ T3 夜间免打扰：纯函数


def entry(at, kind='script', place='', activity='', content='', sender=''):
    metadata = {'life_handoff': {'place': {'value': place}, 'activity': {'value': activity}}} if kind == 'script' \
        else {'senderName': sender}
    return {'kind': kind, 'occurredAt': at.isoformat().replace('+00:00', 'Z'), 'content': content,
            'metadata': json.dumps(metadata, ensure_ascii=False)}


check(vt.is_urgent('急事！小满快醒醒') and vt.is_urgent('救命') and not vt.is_urgent('哈哈哈晚安'), 'urgent')
end = vt.sleep_block_end(date(2026, 10, 3))
check(not vt.night_over(local(2026, 10, 2, 23, 30), TZ), '深夜未结束')
check(not vt.night_over(local(2026, 10, 3, 3, 0), TZ), '凌晨未结束')
morning = datetime.combine(date(2026, 10, 3), end, tzinfo=ZONE).astimezone(timezone.utc)
check(vt.night_over(morning, TZ) and vt.night_over(morning + timedelta(hours=3), TZ), '起床后结束')
state = vt.quiet_inbox_add({}, 3, local(2026, 10, 2, 23, 0))
state = vt.quiet_inbox_add(state, 2, local(2026, 10, 3, 1, 0))
inbox = state['sleep_inbox']
check(inbox['count'] == 5 and inbox['since'].startswith('2026-10-02T15:00') and inbox['last'].startswith('2026-10-02T17:00'), inbox)
check(vt.overnight_context(state, [], local(2026, 10, 3, 2, 0), TZ) is None, '夜里不交付')
msgs = [entry(local(2026, 10, 2, 22, 50), 'group-message', content='睡前的消息', sender='甲')]
msgs += [entry(local(2026, 10, 2, 23, 0) + timedelta(minutes=i), 'group-message', content='夜聊%d' % i, sender='乙')
         for i in range(20)]
msgs += [entry(local(2026, 10, 2, 23, 30), 'script', '卧室床上', '熟睡')]
ctx = vt.overnight_context(state, msgs, morning + timedelta(minutes=5), TZ)
check(ctx['count'] == 5 and ctx['sinceLocal'] == '23:00', ctx)
check(len(ctx['recent']) == vt.OVERNIGHT_MESSAGE_LIMIT and ctx['recent'][-1]['content'] == '夜聊19', ctx['recent'][-1])
check(all(item['content'] != '睡前的消息' for item in ctx['recent']) and ctx['recent'][0]['speaker'] == '乙', '只取入睡后的')
check(vt.overnight_context({}, msgs, morning, TZ) is None, '无积压')
story = {'id': 'sleep-story', 'setting': {'timezone': TZ}}
story_state = {'extensions': {'vitality': state}}
req = vt.request_context(story, story_state, 'advance', morning + timedelta(minutes=5), msgs)
check(req.get('overnightMessages', {}).get('count') == 5, req.keys())
check('overnightMessages' not in vt.request_context(story, story_state, 'user-message', morning + timedelta(minutes=5), msgs),
      '回复回合不交付夜间消息')
new_state, logs = vt.record_turn(story, story_state, 'advance', morning + timedelta(minutes=5), {}, '她醒了')
check(new_state is not None and 'sleep_inbox' not in new_state and any('夜间群消息' in line for line in logs), (new_state, logs))
kept, _ = vt.record_turn(story, story_state, 'advance', local(2026, 10, 3, 2, 0), {}, '熟睡')
check((kept or state).get('sleep_inbox', {}).get('count') == 5, '夜里推进不清空积压')
fields = np_._vitality_fields({'vitality': {'overnightMessages': ctx}})
check(fields.get('overnightMessages') == ctx, '提示词字段透传')
compiled = cc_mod.compile_narrative_context({'interval': {'nowLocal': 'x', 'overnightMessages': ctx}, 'phase': 'advance'}, None, None)
check('overnightMessages' in compiled['authoringWindow']['interval'], '编译保留')
check('interval.overnightMessages' in np_.system_prompt('advance', None, None, '', '', ''), '提示词规则')
print('T3 夜间免打扰纯函数 ✓')

# ============================================================ T4 夜间免打扰：真实 flush_group_turn 闸门


class Proceeded(Exception):
    pass


chunk1.evaluate_group_willingness = lambda previous, config, payload, rng=None: {
    'should_call': True, 'state': {'score': 1.0}, 'probability': 1.0,
    'reason': 'forced-mention' if payload.get('mentioned_bot') else 'always',
}


class FakeService:
    database_resetting = False
    desktop_runtime_phase = 'running'
    rng = None
    auto_advance_config = {}
    runtime_config = {}

    def __init__(self, entries, clock):
        self.entries = entries
        self.clock = clock
        self.story = {'id': 'st', 'status': 'active', 'setting': {'timezone': TZ}, 'state': {}}
        self.buffered_group_turns = {}
        self.narrating_stories = set()
        self.group_willingness = {}
        self.ops = []

    def now(self):
        return self.clock

    def now_ms(self):
        return int(self.clock.timestamp() * 1000)

    async def get_story(self, story_id):
        return self.story

    async def recent_entries(self, story_id, limit=None):
        return self.entries[-(limit or 20):]

    async def serial(self, story_id, task):
        return await task()

    async def db_set(self, table, where, update):
        self.story = {**self.story, **update}

    async def group_cooldown_active(self, *args):
        return False

    def note_group_skip_reason(self, *args):
        self.ops.append(('skip',) + args)
        return True

    def report_operation(self, verbosity, level, story, phase, message, *args):
        self.ops.append((message,) + args)
        if message.startswith('群聊消息准备进入主叙事'):
            raise Proceeded()


for name in ('flush_group_turn',):
    setattr(FakeService, name, getattr(chunk1.ServiceChunk1, name))
FakeService.sleep_resume_at = chunk7.ServiceChunk7.sleep_resume_at


def run_turn(service, contents, mentioned=False, quoted=False):
    service.buffered_group_turns['k'] = {
        'revision': 1, 'story_id': 'st', 'group_id': 'g1', 'rule': {'willingness': {}},
        'messages': [{'content': text, 'speaker': '群友'} for text in contents],
        'mentioned_bot': mentioned, 'quoted_bot': quoted, 'timer': None,
    }
    try:
        asyncio.run(service.flush_group_turn('k', 1))
    except Proceeded:
        return 'called'
    return 'quiet'


asleep = [entry(local(2026, 10, 2, 23, 10), 'script', '卧室床上', '关灯钻进被窝沉沉入睡')]
night = local(2026, 10, 2, 23, 40)
svc = FakeService(asleep, night)
check(run_turn(svc, ['哈哈哈', '还有人醒着吗']) == 'quiet', '睡着时普通群消息不调用主叙事')
inbox = svc.story['state']['extensions']['vitality']['sleep_inbox']
check(inbox['count'] == 2, inbox)
check(run_turn(svc, ['再来一条']) == 'quiet' and svc.story['state']['extensions']['vitality']['sleep_inbox']['count'] == 3, '累计')
check(any('夜间免打扰' in str(op[0]) for op in svc.ops), svc.ops)
check(run_turn(svc, ['小满在吗'], mentioned=True) == 'called', '被 @ 照常进入主叙事')
check(run_turn(svc, ['回你'], quoted=True) == 'called', '被引用照常进入主叙事')
check(run_turn(svc, ['急事！快醒醒']) == 'called', '紧急消息照常进入主叙事')
awake = FakeService([entry(local(2026, 10, 2, 23, 10), 'script', '卧室书桌前', '在书桌前练吉他')], night)
check(run_turn(awake, ['哈哈哈']) == 'called', '醒着照常进入主叙事')
nap = FakeService([entry(local(2026, 10, 3, 14, 0), 'script', '客厅沙发', '窝在沙发上睡着了')], local(2026, 10, 3, 14, 20))
check(run_turn(nap, ['哈哈哈']) == 'called', '白天午睡不拦截')
print('T4 夜间免打扰闸门 ✓')

print(f'test_request_keys_and_sleep: {ok} checks passed')
