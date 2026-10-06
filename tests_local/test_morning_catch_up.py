"""晨间回信 + 私聊夜间免打扰 + 睡眠判定误判修复 测试。

用法：PYTHONPATH=<插件父目录> python test_morning_catch_up.py <插件包名>
"""

import asyncio
import importlib
import json
import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone

PKG = sys.argv[1] if len(sys.argv) > 1 else 'hds_interlude'
tmp = tempfile.mkdtemp()
for name in ('EXPRESSION', 'LIFE_HOOKS', 'STORY_ARCS'):
    os.environ['HDSI_%s_CONFIG' % name] = os.path.join(tmp, name.lower() + '.json')

cu = importlib.import_module(f'{PKG}.core.catch_up')
vt = importlib.import_module(f'{PKG}.core.vitality')
chunk1 = importlib.import_module(f'{PKG}.core.service.chunk1')
chunk4 = importlib.import_module(f'{PKG}.core.service.chunk4')
ss = importlib.import_module(f'{PKG}.core.story_state')
UTC = timezone.utc
ok = 0


def check(cond, msg):
    global ok
    if not cond:
        raise AssertionError(msg)
    ok += 1


def at(text):
    return datetime.fromisoformat(text.replace('Z', '+00:00'))


def script(when, activity, entry_id=1):
    return {'id': entry_id, 'kind': 'script', 'occurredAt': when,
            'metadata': {'life_handoff': {'place': {'value': '卧室'}, 'activity': {'value': activity}}}}


# ============================================================ T1 睡眠判定：别人 / 猫在睡不算她睡（10-05 晚实测误判）
cases = {
    '在群里笑回关苍蝇故事后喝温水看猫睡觉': False,
    '在转椅里喝温水看猫睡觉放空': False,
    '喝完温水后摸摸熟睡的煤球并在转椅里伸懒腰放空': False,
    '洗漱后靠在转椅里喝水打哈欠放空准备入睡': True,
    '盖着温暖的软被沉沉入睡': True,
    '国庆假期清晨在暖被里香甜酣睡赖床': True,
    '抱着煤球睡着了': True,
    '陪煤球一起睡觉': True,
    '醒来后伸懒腰': False,
    '': False,
}
for activity, expected in cases.items():
    check(vt.is_asleep(activity) is expected, '%r → %s' % (activity, vt.is_asleep(activity)))
check(cu.asleep_in_story([script('2026-10-05T14:14:00Z', '喝温水看猫睡觉')]) is False, '晚上看猫睡觉：醒着')
check(cu.asleep_in_story([script('2026-10-05T20:01:00Z', '盖着温暖的软被沉沉入睡')]) is True, '睡着')
check(cu.asleep_in_story([{'kind': 'user-message'}]) is None and cu.asleep_in_story([]) is None, '没有交接信息：不下结论')
print('T1 睡眠判定 ✓')

# ============================================================ T2 醒来检测与排队（纯函数）
NIGHT = at('2026-10-05T16:40:00Z')    # 00:40 入睡
WAKE = at('2026-10-06T01:25:00Z')     # 09:25 醒来


def participant(pid, name, last_user, pending=3, last_char='2026-10-05T07:23:36Z', status='active'):
    return {'id': pid, 'displayName': name, 'status': status, 'userId': pid.rsplit(':', 1)[-1],
            'state': {'unreadMessageCount': pending, 'pendingReplyCount': pending,
                      'lastUserMessageAt': last_user, 'lastCharacterMessageAt': last_char}}


FANG = participant('onebot:1:458593826', '好小狗-放映手机', '2026-10-05T20:01:49Z')
ME = participant('onebot:1:269502169', '　', '2026-10-05T22:30:00Z', pending=1)
REPLIED = participant('onebot:1:3', '已回', '2026-10-05T20:00:00Z', last_char='2026-10-05T21:00:00Z')
OLD = participant('onebot:1:4', '前天', '2026-10-04T10:00:00Z')
NONE = participant('onebot:1:5', '没消息', '2026-10-05T20:00:00Z', pending=0)
INACTIVE = participant('onebot:1:6', '停用', '2026-10-05T20:00:00Z', status='paused')
EARLY = participant('onebot:1:7', '睡前', '2026-10-05T16:10:00Z')   # 入睡前半小时
PEOPLE = [FANG, ME, REPLIED, OLD, NONE, INACTIVE, EARLY]

state, drafts = cu.plan({}, True, NIGHT, PEOPLE)
check(state == {'asleep_since': '2026-10-05T16:40:00.000Z'} and drafts == [], '睡着：记下入睡时刻')
state2, _ = cu.plan({'extensions': {'catch_up': state}}, True, NIGHT + timedelta(hours=3), PEOPLE)
check(state2['asleep_since'] == state['asleep_since'], '一直睡着：入睡时刻不变')
check(cu.plan({'extensions': {'catch_up': state}}, None, WAKE, PEOPLE) == (state, []), '说不准：什么都不改')
trusted = lambda p: 'high' if p['id'].endswith('269502169') else ''
woke, drafts = cu.plan({'extensions': {'catch_up': state}}, False, WAKE, PEOPLE, trusted, 'Asia/Shanghai', lambda: 0.5)
check('asleep_since' not in woke and woke['last_wake_at'] == '2026-10-06T01:25:00.000Z', woke)
ids = [d['participantId'] for d in drafts]
check(ids == ['onebot:1:269502169', 'onebot:1:7', 'onebot:1:458593826'],
      '只排还在等的人；最信任的排第一，其余按来信先后: %s' % ids)
times = [at(d['notBefore']) for d in drafts]
check(times[0] == WAKE + timedelta(minutes=4.5) and times[1] - times[0] == timedelta(minutes=6), '错峰: %s' % times)
fang = drafts[2]
check(fang['type'] == 'follow-up-commitment' and fang['payload']['morningCatchUp'] is True
      and fang['payload']['requiresVisibleOutcome'] is True and fang['payload']['messageCount'] == 3, fang)
check('好小狗-放映手机' in fang['summary'] and '凌晨 4:01' in fang['summary'] and '睡着了没看到' in fang['summary'], fang['summary'])
check('对方' in drafts[0]['summary'] and '269502169' not in drafts[0]['summary'], '空白昵称不用 QQ 号')
check(at(fang['payload']['expiresAt']) == WAKE + timedelta(hours=6), '6 小时过期')
check(cu.plan({'extensions': {'catch_up': woke}}, False, WAKE + timedelta(minutes=1), PEOPLE) == (woke, []), '醒来后的下一次扫描：不重复排')
nap, d = cu.plan({'extensions': {'catch_up': {'asleep_since': '2026-10-05T14:14:00.000Z'}}}, False,
                 at('2026-10-05T14:31:00Z'), PEOPLE)
check(d == [] and 'asleep_since' not in nap and 'last_wake_at' not in nap, '睡了 17 分钟（误判 / 打盹）：不算醒来')
print('T2 醒来检测与排队 ✓')

# ============================================================ T3 服务层：后台扫描里排回访，不重复


class Host:
    rng = staticmethod(lambda: 0.0)
    runtime_config = {}

    def __init__(self, story_state, entries, people, pending=()):
        self.story = {'id': 'st', 'state': ss.encode_story_state(story_state),
                      'setting': {'timezone': 'Asia/Shanghai'}}
        self.entries, self.people = entries, people
        self.intents = [dict(item) for item in pending]
        self.wakes, self.logs = [], []

    async def recent_entries(self, story_id, limit):
        return self.entries

    async def participants(self, story_id):
        return self.people

    async def get_story(self, story_id):
        return self.story

    async def db_set(self, table, where, values):
        self.story = {**self.story, **values}

    async def db_get(self, table, query, options=None):
        return [i for i in self.intents if all(i.get(k) == v for k, v in query.items() if k != 'storyId')]

    async def append_intent(self, story_id, intent, now, participant_id):
        self.intents.append({**intent, 'participantId': participant_id, 'status': 'pending'})

    def schedule_due_intent_wake(self, story_id, when):
        self.wakes.append(when)

    def _access_config(self):
        return {'user_accounts': [{'qq': '269502169', 'trust': 'high'}]}

    def report_operation(self, *args):
        self.logs.append(args[4] % args[5:] if len(args) > 5 else args[4])

    def report_standalone(self, *args):
        self.logs.append('WARN ' + str(args))


Host._morning_catch_up = chunk4.ServiceChunk4._morning_catch_up
asleep_entries = [script('2026-10-06T00:45:00Z', '国庆假期早晨在暖被里香甜酣睡')]
awake_entries = [script('2026-10-06T01:25:00Z', '揉着眼睛醒来，靠在床头看手机')]
host = Host({}, asleep_entries, PEOPLE)
asyncio.run(host._morning_catch_up(host.story, NIGHT))
check(ss.decode_story_state(host.story['state'])['extensions']['catch_up']['asleep_since'], '扫描时记下睡着')
host.entries = awake_entries
asyncio.run(host._morning_catch_up(host.story, WAKE))
check([i['participantId'] for i in host.intents] == ['onebot:1:269502169', 'onebot:1:7', 'onebot:1:458593826'], host.intents)
check(len(host.wakes) == 3 and any('她醒了 等待回复的私聊=3' in line for line in host.logs), host.logs)
asyncio.run(host._morning_catch_up(host.story, WAKE + timedelta(minutes=1)))
check(len(host.intents) == 3, '下一分钟的扫描不重复排')
# 已经有一条待处理的晨间回访（比如上一次醒来排的还没处理）：不重复
again = Host({'extensions': {'catch_up': {'asleep_since': '2026-10-05T16:40:00.000Z'}}}, awake_entries, [FANG],
             pending=[{'participantId': FANG['id'], 'type': 'follow-up-commitment', 'status': 'pending',
                       'payload': {'morningCatchUp': True}}])
asyncio.run(again._morning_catch_up(again.story, WAKE))
check(len(again.intents) == 1, '已有待处理的晨间回访：不重复')
off = Host({'extensions': {'catch_up': {'asleep_since': '2026-10-05T16:40:00.000Z'}}}, awake_entries, PEOPLE)
off.runtime_config = {'morningCatchUp': False}
asyncio.run(off._morning_catch_up(off.story, WAKE))
check(off.intents == [], '开关关闭')


class Broken(Host):
    async def participants(self, story_id):
        raise RuntimeError('db down')


broken = Broken({'extensions': {'catch_up': {'asleep_since': '2026-10-05T16:40:00.000Z'}}}, awake_entries, PEOPLE)
check(asyncio.run(broken._morning_catch_up(broken.story, WAKE)) is broken.story and any('WARN' in x for x in broken.logs),
      '出错只 warn，不影响后台推进')
# 回访那一回合她已经回过了，但模型没交 followUpResolutions（10-06 #14 实测）：下一次扫描据实结清
fang_replied = participant('onebot:1:458593826', '好小狗-放映手机', '2026-10-05T20:01:49Z', last_char='2026-10-06T01:45:46Z')
settle = Host({}, awake_entries, [fang_replied, ME],
              pending=[{'id': 14, 'participantId': fang_replied['id'], 'type': 'follow-up-commitment', 'status': 'pending',
                        'createdAt': '2026-10-06T01:20:12.223Z', 'payload': {'manual': 'morning-catch-up 2026-10-06'}},
                       {'id': 15, 'participantId': ME['id'], 'type': 'follow-up-commitment', 'status': 'pending',
                        'createdAt': '2026-10-06T01:30:00Z', 'payload': {'morningCatchUp': True}},
                       {'id': 16, 'participantId': fang_replied['id'], 'type': 'follow-up-commitment', 'status': 'pending',
                        'createdAt': '2026-10-06T01:20:00Z', 'payload': {'kind': 'reply'}}])
settle_sets = []
_orig = settle.db_set


async def tracking(table, where, values):
    if table == 'interlude_intent':
        settle_sets.append((where['id'], values['status']))
    else:
        await _orig(table, where, values)


settle.db_set = tracking
asyncio.run(settle._morning_catch_up(settle.story, WAKE + timedelta(minutes=21)))
check(settle_sets == [(14, 'completed')], '回过了的晨间回访结清；没回的、非晨间回访的承诺不动: %s' % settle_sets)
check(any('回访结清' in line for line in settle.logs), settle.logs)
check(cu.is_catch_up_intent({'payload': '{"morningCatchUp": true}'}) and not cu.is_catch_up_intent({'payload': {}}), '字符串 payload')
check(not cu.answered_since(ME, '2026-10-06T01:30:00Z') and cu.answered_since(fang_replied, '2026-10-06T01:20:12Z'), 'answered_since')
print('T3 服务层排队 ✓')

# ============================================================ T4 私聊夜间免打扰：静默入库省调用，紧急穿透


class Session(dict):
    pass


class PrivateHost:
    runtime_config = {}
    config = {}

    def __init__(self, asleep=True):
        self.asleep = asleep
        self.buffered, self.logs, self.entries = [], [], []
        self.story = {'id': 'st', 'status': 'active', 'setting': {'timezone': 'Asia/Shanghai'}}
        self.participant = {'id': 'onebot:1:458593826', 'status': 'active'}

    async def sleep_resume_at(self, story, now):
        return datetime(2026, 10, 6, 1, 0, tzinfo=UTC) if self.asleep else None

    def buffer_user_narrative(self, *args, **kwargs):
        self.buffered.append(args)

    def report_operation(self, *args):
        self.logs.append(args[4] % args[5:] if len(args) > 5 else args[4])


# 直接驱动 receive() 里加的那一段：取出与 receive 中相同的判断函数（同一份代码路径的最小复现）
src = open(chunk1.__file__, encoding='utf-8').read()
check("'夜间免打扰：她已睡着，私聊消息已入库不调用主叙事，醒来后回信" in src
      and src.index('夜间免打扰：她已睡着，私聊消息') < src.index("self.buffer_user_narrative(\n            accepted['story']"),
      '免打扰判断在 buffer_user_narrative（模型调用）之前')
check("record_incoming_message" in src and src.index('record_incoming_message') < src.index('夜间免打扰：她已睡着，私聊消息'),
      '未读 / 待回计数与入库在判断之前已完成（醒来能回）')


async def gate(host, content):
    """与 receive() 中的判断逐行一致。"""
    if bool(chunk1._config_value(host.runtime_config, 'nightQuietPrivate', 'night_quiet_private', True)) \
            and not vt.is_urgent(str(content or '')):
        if await host.sleep_resume_at(host.story, None) is not None:
            host.logs.append('quiet')
            return True
    host.buffer_user_narrative(content)
    return True


h = PrivateHost()
asyncio.run(gate(h, '其实你是米线'))
check(h.buffered == [] and h.logs == ['quiet'], '睡着 + 普通消息：不调用主叙事')
asyncio.run(gate(h, '急事！快醒醒'))
check(len(h.buffered) == 1, '紧急消息穿透')
awake = PrivateHost(asleep=False)
asyncio.run(gate(awake, '在吗'))
check(len(awake.buffered) == 1, '醒着：照常')
off = PrivateHost()
off.runtime_config = {'night_quiet_private': False}
asyncio.run(gate(off, '在吗'))
check(len(off.buffered) == 1, '开关关闭：照常')
# 夜间的群消息门也受益于误判修复：晚上看猫睡觉时不再被判成睡着
evening = [script('2026-10-05T14:14:00Z', '在群里笑回关苍蝇故事后喝温水看猫睡觉')]
check(vt.sleep_resume_at(evening, at('2026-10-05T14:15:00Z'), 'Asia/Shanghai') is None, '22:14 看猫睡觉：不进免打扰')
night = [script('2026-10-05T16:40:00Z', '洗漱后靠在转椅里喝水打哈欠放空准备入睡')]
check(vt.sleep_resume_at(night, at('2026-10-05T17:00:00Z'), 'Asia/Shanghai') is not None, '真睡着：免打扰')
print('T4 私聊夜间免打扰 ✓')

print(f'test_morning_catch_up: {ok} checks passed')
