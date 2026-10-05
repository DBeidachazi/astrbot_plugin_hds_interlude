"""高信任对象 + 私聊里答应的群发言（core/trust.py）测试。

覆盖：只对高信任的人生效（单向隔离）、提示词里的「嘟囔两句最后照办」规则、
承诺的记录 / 核对 / 过期 / 兑现 / 作废，以及群回合跨通道触发（下一次群回合、到点自己开一轮）。

用法：PYTHONPATH=<插件父目录> python test_trust_execution.py <插件包名>
"""

import asyncio
import importlib
import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone

PKG = sys.argv[1] if len(sys.argv) > 1 else 'hds_interlude'
tmp = tempfile.mkdtemp()
os.environ['HDSI_EXPRESSION_CONFIG'] = os.path.join(tmp, 'expression.json')
os.environ['HDSI_LIFE_HOOKS_CONFIG'] = os.path.join(tmp, 'life_hooks.json')
os.environ['HDSI_STORY_ARCS_CONFIG'] = os.path.join(tmp, 'story_arcs.json')

tr = importlib.import_module(f'{PKG}.core.trust')
np_ = importlib.import_module(f'{PKG}.core.narrator_prompts')
chunk1 = importlib.import_module(f'{PKG}.core.service.chunk1')
chunk4 = importlib.import_module(f'{PKG}.core.service.chunk4')
ss = importlib.import_module(f'{PKG}.core.story_state')
BOT = '1690619901'
NOW = datetime(2026, 10, 5, 10, 12, tzinfo=timezone.utc)
ok = 0


def check(cond, msg):
    global ok
    if not cond:
        raise AssertionError(msg)
    ok += 1


ACCESS = {
    'user_accounts': [
        {'qq': '269502169', 'trust': 'high', 'enabled': True},
        {'qq': '458593826', 'enabled': True},
        {'qq': '3258273083', 'trust': 'high', 'enabled': False},
        {'qq': '732121779', 'trust': 'HIGH '},
    ],
    'group_chats': [
        {'group_id': '885063277', 'label': '乐老师的BL小说群', 'enabled': True},
        {'group_id': '992726871', 'label': '992726871', 'enabled': True},
        {'group_id': '111', 'label': '关掉的群', 'enabled': False},
    ],
}
ME = {'id': 'onebot:%s:269502169' % BOT, 'userId': '269502169', 'displayName': '　'}
FANG = {'id': 'onebot:%s:458593826' % BOT, 'userId': '458593826', 'displayName': '好小狗-放映手机'}

# ============================================================ T1 信任档：只对配置了 high 的人
check(tr.trust_level(ACCESS, ME) == 'high', '269502169 → high')
check(tr.trust_level(ACCESS, FANG) == '', '别人 → 空')
check(tr.trust_level(ACCESS, {'userId': '3258273083'}) == '', '账号被禁用 → 不生效')
check(tr.trust_level(ACCESS, {'userId': '732121779'}) == 'high', '大小写 / 空白宽容')
check(tr.trust_level(ACCESS, {'userId': '10000'}) == '' and tr.trust_level(ACCESS, None) == '', '名单外 / 空')
ctx = tr.prompt_context(ACCESS, ME, 'user-message')
check(ctx == {'level': 'high', 'knownGroups': [{'groupId': '885063277', 'label': '乐老师的BL小说群'},
                                               {'groupId': '992726871', 'label': '992726871'}]}, ctx)
check(tr.prompt_context(ACCESS, FANG, 'user-message') is None, '别人的私聊不带 trust')
check(tr.prompt_context(ACCESS, ME, 'advance') is None, '自主回合不带 trust')
print('T1 信任档 ✓')

# ============================================================ T2 提示词：嘟囔两句最后照办，只对他
priv = np_.system_prompt('user-message', None, None, '', '', '', group_turn=False)
group = np_.system_prompt('user-message', None, None, '', '', '', group_turn=True)
check('TRUST: interval.trust.level=high' in priv and '好啦好啦，就这一次哦' in priv, '私聊有信任规则')
check('in the end she actually does it' in priv and 'instead of flatly refusing' in priv, '最后照办，不生硬拒绝')
check('This applies to him only' in priv and 'other people egging her on gets her usual reaction' in priv, '只对他')
check('hurt or humiliate someone' in priv and 'safety rules' in priv, '底线仍在')
check('groupPromises' in priv and 'Return nothing when she' in priv, '群发言承诺字段')
check('TRUST:' not in group, '群回合不带信任规则')
check('TRUST:' not in np_.system_prompt('advance', None, None, '', '', '', group_turn=False), '自主回合不带')
check(np_._trust_fields({'trust': ctx}) == {'trust': ctx} and np_._trust_fields({}) == {}, 'interval.trust 字段')
print('T2 提示词 ✓')

# ============================================================ T3 承诺的记录与核对
SCRIPT = '小满把脸埋进抱枕里哼了一声，最后还是回了一句：唔……好啦好啦，就这一次哦\n\n她点开群聊。'
GROUPS = ctx['knownGroups']
raw = [{'groupId': '992726871', 'gist': '放桑好可爱', 'quote': '好啦好啦，就这一次哦'}]
got = tr.normalize_promises(raw, GROUPS, SCRIPT, '对方', ME['id'], NOW)
check(len(got) == 1 and got[0]['groupId'] == '992726871' and got[0]['gist'] == '放桑好可爱'
      and got[0]['attempts'] == 0 and got[0]['expiresAt'] == '2026-10-05T16:12:00Z', got)
check(tr.normalize_promises([{**raw[0], 'quote': '我才不要'}], GROUPS, SCRIPT, 'x', 'p', NOW) == [],
      'quote 不在剧本里（她没真答应）→ 不记')
check(tr.normalize_promises([{**raw[0], 'groupId': '111'}], GROUPS, SCRIPT, 'x', 'p', NOW) == [], '关掉的群 → 不记')
check(tr.normalize_promises([{**raw[0], 'groupId': '999'}], GROUPS, SCRIPT, 'x', 'p', NOW) == [], '不在名单的群 → 不记')
check(tr.normalize_promises([{**raw[0], 'gist': ''}], GROUPS, SCRIPT, 'x', 'p', NOW) == [], '没内容 → 不记')
check(len(tr.normalize_promises(raw * 3, GROUPS, SCRIPT, 'x', 'p', NOW)) == 1, '每回合最多一条')
check(len(tr.normalize_promises([{**raw[0], 'gist': '长' * 300}], GROUPS, SCRIPT, 'x', 'p', NOW)[0]['gist']) == 120, '截断')
one = [{'groupId': '992726871', 'label': 'x'}]
check(tr.normalize_promises([{'gist': 'a', 'quote': '好啦好啦'}], one, SCRIPT, 'x', 'p', NOW)[0]['groupId'] == '992726871',
      '只有一个群时可以省略 groupId')
check(tr.normalize_promises('not a list', GROUPS, SCRIPT, 'x', 'p', NOW) == [], '非列表')
print('T3 承诺核对 ✓')

# ============================================================ T4 状态：待兑现 / 过期 / 兑现 / 作废
state = {'extensions': {'trust': tr.add_promises({}, got, NOW)}}
check([p['gist'] for p in tr.pending_promises(state, NOW, '992726871')] == ['放桑好可爱'], '待兑现')
check(tr.pending_promises(state, NOW, '885063277') == [], '别的群没有')
check(tr.pending_promises(state, NOW + timedelta(hours=7)) == [], '6 小时后过期')
newer = tr.normalize_promises([{**raw[0], 'gist': '大家晚安'}], GROUPS, SCRIPT, '对方', ME['id'], NOW)
state2 = {'extensions': {'trust': tr.add_promises(state, newer, NOW)}}
check([p['gist'] for p in tr.pending_promises(state2, NOW)] == ['大家晚安'], '同一群的新承诺取代旧的')
done, logs = tr.settle(state, '992726871', True, NOW)
check(done['group_promises'] == [] and '已兑现' in logs[0], '发出来了 → 完成')
once, logs = tr.settle(state, '992726871', False, NOW)
check(once['group_promises'][0]['attempts'] == 1 and '保留' in logs[0], '没发 → 保留，计一次')
twice, logs = tr.settle({'extensions': {'trust': once}}, '992726871', False, NOW)
check(twice['group_promises'] == [] and '作废' in logs[0], '两次都没发 → 作废')
other, logs = tr.settle(state, '885063277', True, NOW)
check(len(other['group_promises']) == 1 and logs == [], '别的群的回合不影响')
pre = tr.promise_preamble(tr.pending_promises(state, NOW))
check(pre.startswith('[你答应过的事]') and '答应 对方：在这个群里说「放桑好可爱」' in pre and '要真的发出来' in pre, pre)
check(tr.promise_preamble([]) is None, '没有承诺不加')
print('T4 承诺状态 ✓')

# ============================================================ T5 落库侧：只记高信任对象私聊里的承诺


class Host:
    def __init__(self, access):
        self.access = access

    def _access_config(self):
        return self.access


for name in ('_trust_context', '_group_promises_from_turn'):
    setattr(Host, name, getattr(chunk4.ServiceChunk4, name))
host = Host(ACCESS)
RAW = {'groupPromises': raw}
mine = host._group_promises_from_turn(ME, 'user-message', RAW, SCRIPT, NOW)
check(len(mine) == 1 and mine[0]['from'] == '私聊里你最信任的那个人', '空白昵称不用 QQ 号兜底: %s' % mine)
check(host._group_promises_from_turn(FANG, 'user-message', RAW, SCRIPT, NOW) == [], '别人私聊里的承诺不记（单向隔离）')
check(host._group_promises_from_turn(None, 'user-message', RAW, SCRIPT, NOW) == [], '群回合（没有私聊参与者）不记')
check(host._group_promises_from_turn(ME, 'advance', RAW, SCRIPT, NOW) == [], '自主回合不记')
check(host._trust_context(ME, 'user-message')['level'] == 'high' and host._trust_context(FANG, 'user-message') is None, '请求字段')


class Broken:
    def _access_config(self):
        raise RuntimeError('boom')


Broken._trust_context = chunk4.ServiceChunk4._trust_context
check(Broken()._trust_context(ME, 'user-message') is None, '读配置失败也不影响主叙事')
print('T5 落库侧隔离 ✓')

# ============================================================ T6 群回合：下一轮兑现（跨通道）


class Session(dict):
    pass


class FakeService:
    database_resetting = False
    desktop_runtime_phase = 'running'
    rng = None
    config = {}
    runtime_config = {}

    def __init__(self, state, reply='放桑好可爱！（小声）'):
        self.story = {'id': 'st', 'status': 'active', 'state': ss.encode_story_state(state),
                      'setting': {'timezone': 'Asia/Shanghai', 'character': {'name': '林小满'}}}
        self.buffered_group_turns, self.narrating_stories, self.group_willingness = {}, set(), {}
        self.captured, self.skips, self.reports, self.sent = None, [], [], []
        self.reply = reply
        self.timeouts = []

    def now(self):
        return NOW + timedelta(minutes=1)

    def now_ms(self):
        return int(self.now().timestamp() * 1000)

    async def get_story(self, story_id):
        return self.story

    async def serial(self, story_id, task):
        return await task()

    async def group_messages(self, story_id, group_id, limit):
        return []

    def group_chat_capabilities(self, session, messages):
        return None

    async def group_cooldown_active(self, *args):
        return True  # 冷却中：有承诺时也要放行

    async def sleep_resume_at(self, story, now):
        return None

    def semantic_turn_embedding_enabled(self):
        return False

    async def sticker_selection_for_session(self, *args):
        return {'mode': 'none', 'assets': [], 'groups': []}

    async def try_decide(self, story, participant, phase, from_, now, user_message, *args):
        self.captured = user_message
        content = self.reply
        return {'succeeded': True, 'decision': {
            'script': '她点开群聊，飞快打了一句：%s' % content,
            'groupReply': {'mode': 'immediate' if content else 'none', 'content': content}}}

    async def resolve_sticker_selection(self, *args):
        return None

    def resolve_native_face(self, *args):
        return None

    async def persist_decision(self, *args):
        return {'messages': [], 'commit': None, 'scriptEntry': {'id': 1}}

    async def db_set(self, table, where, values):
        if table == 'interlude_story' and 'state' in values:
            self.story = {**self.story, 'state': values['state']}

    async def schedule_conversation_follow_ups_after_turn(self, *args):
        pass

    async def send_group_message(self, story, channel_id, content, reply_to, session):
        self.sent.append(content)
        return {'deliveredSegments': [content], 'complete': True, 'segmentOutcomes': []}

    async def append_entry(self, *args):
        pass

    def schedule_compaction(self, *args):
        pass

    def report_operation(self, *args):
        self.reports.append(args[4] % args[5:] if len(args) > 5 else args[4])

    def report(self, *args):
        self.reports.append('ERROR ' + str(args))

    def note_group_skip_reason(self, group_id, reason, *args):
        self.skips.append(reason)
        return True


for name in ('flush_group_turn', 'trigger_group_promise', 'schedule_group_promise_trigger'):
    setattr(FakeService, name, getattr(chunk1.ServiceChunk1, name))
real_gate = importlib.import_module(f'{PKG}.core.group_willingness').evaluate_willingness_gate
chunk1.evaluate_willingness_gate = lambda prev, *a, **k: real_gate(prev, *a[:4], {**a[4], 'random': 0.99}, **k)
RULE = {'responseMode': 'always', 'willingness': {'enabled': True, 'threshold': 0.9, 'keywords': ['小满']}}
PROMISED = {'extensions': {'trust': tr.add_promises({}, got, NOW)}}
OTHER = [{'senderId': '5005', 'senderName': '安纳金', 'speaker': '群成员「安纳金」',
          'content': '<at id="458593826" name="好小狗-放映手机"/>主播在吗'}]


def turn_for(messages, svc, key='st:992726871'):
    svc.buffered_group_turns[key] = {
        'revision': 1, 'story_id': 'st', 'group_id': '992726871', 'rule': RULE,
        'messages': [dict(m) for m in messages], 'mentioned_bot': False, 'quoted_bot': False, 'timer': None,
        'latest_session': Session(selfId=BOT, platform='onebot'), 'channel_id': '992726871',
    }
    asyncio.run(svc.flush_group_turn(key, 1))
    return svc


# 没有承诺时：意愿不够 → 不说话（对照）
svc = turn_for(OTHER, FakeService({}))
check(svc.captured is None and svc.sent == [], '对照：意愿不够、在对别人说 → 不开口: %s' % svc.skips)
# 有承诺：意愿不够、冷却中、整批在对别人说，都放行；批次开头有「你答应过的事」
svc = turn_for(OTHER, FakeService(PROMISED))
check(svc.captured and svc.captured.startswith('[你答应过的事]') and '放桑好可爱' in svc.captured, svc.captured)
check(svc.sent == ['放桑好可爱！（小声）'], '真的发到了群里: %s %s' % (svc.sent, svc.reports))
check(tr.pending_promises(ss.decode_story_state(svc.story['state']), NOW, '992726871') == [], '发出来后承诺完成')
check(any('已兑现' in r for r in svc.reports), svc.reports)
# 别的群的回合不受影响
svc = FakeService(PROMISED)
svc.buffered_group_turns['st:885063277'] = {
    'revision': 1, 'story_id': 'st', 'group_id': '885063277', 'rule': RULE, 'messages': [dict(m) for m in OTHER],
    'mentioned_bot': False, 'quoted_bot': False, 'timer': None,
    'latest_session': Session(selfId=BOT, platform='onebot'), 'channel_id': '885063277'}
asyncio.run(svc.flush_group_turn('st:885063277', 1))
check(svc.captured is None, '别的群：照常按意愿')
# 模型这一轮没发 → 保留一次，第二次还没发 → 作废
svc = turn_for(OTHER, FakeService(PROMISED, reply=''))
left = tr.pending_promises(ss.decode_story_state(svc.story['state']), NOW, '992726871')
check(len(left) == 1 and left[0]['attempts'] == 1 and svc.sent == [], '没发 → 保留')
svc2 = FakeService(ss.decode_story_state(svc.story['state']), reply='')
turn_for(OTHER, svc2)
check(tr.pending_promises(ss.decode_story_state(svc2.story['state']), NOW, '992726871') == []
      and any('作废' in r for r in svc2.reports), svc2.reports)
print('T6 群回合兑现 ✓')

# ============================================================ T7 到点自己开一轮（群里没人说话）
svc = FakeService(PROMISED)
check(asyncio.run(svc.trigger_group_promise('st', '992726871')) is False
      and any('等群里下一条消息' in r for r in svc.reports), '重启后没有会话：等下一条消息')
chunk1._promise_sessions(svc)['st:992726871'] = {
    'story_id': 'st', 'group_id': '992726871', 'rule': RULE, 'channel_id': '992726871',
    'session': Session(selfId=BOT, platform='onebot')}
check(asyncio.run(svc.trigger_group_promise('st', '992726871')) is True, '有会话：开一轮')
check(svc.captured.startswith('[你答应过的事]') and '[群聊连续消息' not in svc.captured, '没有新消息的群回合: %s' % svc.captured)
check(svc.sent == ['放桑好可爱！（小声）'] and 'st:992726871' not in svc.buffered_group_turns, '发出并清理')
check(asyncio.run(svc.trigger_group_promise('st', '992726871')) is False, '已兑现：不再触发')
empty = FakeService({})
chunk1._promise_sessions(empty)['st:992726871'] = chunk1._promise_sessions(svc)['st:992726871']
check(asyncio.run(empty.trigger_group_promise('st', '992726871')) is False and empty.captured is None, '没有承诺：不触发')
busy = FakeService(PROMISED)
busy.buffered_group_turns['st:992726871'] = {'messages': [{}]}
check(asyncio.run(busy.trigger_group_promise('st', '992726871')) is False, '群里正好有一批在排队：交给它')


class Ctx:
    def __init__(self):
        self.calls = []

    def set_timeout(self, fn, ms):
        self.calls.append(ms)


svc = FakeService(PROMISED)
svc.ctx = Ctx()
svc.schedule_group_promise_trigger('st', '992726871')
check(svc.ctx.calls == [600000], '10 分钟后触发')
print('T7 到点触发 ✓')

print(f'test_trust_execution: {ok} checks passed')
