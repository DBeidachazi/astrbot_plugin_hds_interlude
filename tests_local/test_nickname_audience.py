"""群内称呼（「主播」不是她）与频控窗口取「她自己的消息」测试。

用法：PYTHONPATH=<插件父目录> python test_nickname_audience.py <插件包名>
"""

import asyncio
import importlib
import os
import sys
import tempfile

PKG = sys.argv[1] if len(sys.argv) > 1 else 'hds_interlude'
tmp = tempfile.mkdtemp()
os.environ['HDSI_EXPRESSION_CONFIG'] = os.path.join(tmp, 'expression.json')
os.environ['HDSI_LIFE_HOOKS_CONFIG'] = os.path.join(tmp, 'life_hooks.json')
os.environ['HDSI_STORY_ARCS_CONFIG'] = os.path.join(tmp, 'story_arcs.json')

gd = importlib.import_module(f'{PKG}.core.group_digest')
ex = importlib.import_module(f'{PKG}.core.expression')
np_ = importlib.import_module(f'{PKG}.core.narrator_prompts')
chunk1 = importlib.import_module(f'{PKG}.core.service.chunk1')
chunk4 = importlib.import_module(f'{PKG}.core.service.chunk4')
vitality = importlib.import_module(f'{PKG}.core.vitality')
BOT = '1690619901'
ALIASES = ['林小满', '小满']
CONFIG = '主播=好小狗-放映手机(458593826)'
ok = 0


def check(cond, msg):
    global ok
    if not cond:
        raise AssertionError(msg)
    ok += 1


def msg(sender, name, content):
    return {'senderId': sender, 'senderName': name, 'speaker': '群成员「%s」' % name, 'content': content}


# 2026-10-05 15:25 现场（entry 5376–5387）
SCENE = [
    msg('3188754042', '挂科学-Cody', '因为我感觉我有点完蛋了'),
    msg('3188754042', '挂科学-Cody', '觉得MBTI里的那个人格标签和自己全中了'),
    msg('846727248', '职场霸凌所有人-多多', '我都睡醒了主播怎么还在'),
    msg('846727248', '职场霸凌所有人-多多', '[图片]'),
    msg('2002', '美术-匙老师', '<at id="643482276" name="迷子"/>你咋还看术立口呢？'),
]

# ============================================================ T1 称呼配置解析
nick = gd.parse_nicknames(CONFIG)
check(nick == [{'nickname': '主播', 'member': '好小狗-放映手机', 'qq': '458593826'}], nick)
multi = gd.parse_nicknames('主播/放=好小狗-放映手机（458593826）\n群主=乐老师\n没有等号的行\n=空称呼')
check([(i['nickname'], i['member'], i['qq']) for i in multi] ==
      [('主播', '好小狗-放映手机', '458593826'), ('放', '好小狗-放映手机', '458593826'), ('群主', '乐老师', '')], multi)
check(gd.parse_nicknames([{'nickname': '主播', 'member': 'A', 'qq': '1'}, {'nickname': ''}]) ==
      [{'nickname': '主播', 'member': 'A', 'qq': '1'}], '列表形式')
check(gd.parse_nicknames('') == [] and gd.parse_nicknames(None) == [], '空配置')
pre = gd.nickname_preamble(nick, '林小满')
check(pre == '[群内称呼] 「主播」= 好小狗-放映手机。这些是群友之间的叫法，都不是在叫林小满。', pre)
check(gd.nickname_preamble([], '林小满') is None, '没配置不加')
print('T1 称呼配置 ✓')

# ============================================================ T2 对象判断与标注
aud = gd.message_audiences(SCENE, {BOT}, ALIASES, (), nick)
check(aud[2] == 'other:458593826' and aud[3] == 'other:458593826', '「主播怎么还在」→ 在说好小狗；图片沿用: %s' % aud)
check(aud[0] is None and aud[4] == 'other:643482276', aud)
names = gd.name_map(SCENE)
note = gd.audience_note(aud[2], names, SCENE[2]['content'], nick)
check(note == '（↑ 「主播」在这个群里指 好小狗-放映手机，不是你；这条不是在跟你说话）', note)
# 没配置称呼时：通用称谓兜底
aud0 = gd.message_audiences(SCENE, {BOT}, ALIASES)
check(aud0[2] is None, '没配置时是泛泛的消息')
note0 = gd.audience_note(aud0[2], names, SCENE[2]['content'])
check(note0 == '（↑ 这条没有 @ 谁，也没叫你的名字：里面的「主播」说的是别人，不是你）', note0)
check(gd.audience_note(None, names, '今天好热') == '', '没有称谓不加')
check(gd.audience_note('her', names, '小满主播') == '（↑ 这条是在找你）', '叫了她的名字：找她优先')
called = [msg('846727248', '多多', '小满你是主播吗')]
check(gd.message_audiences(called, {BOT}, ALIASES, (), nick) == ['her'], '直接问她：仍然是找她')
at_other = [msg('5005', '安纳金', '<at id="458593826" name="好小狗-放映手机"/>主播主播有空的话可以买个星战前线2玩玩')]
check(gd.message_audiences(at_other, {BOT}, ALIASES, (), nick) == ['other:458593826'], '@ 优先')
check(not gd.addressed_elsewhere_only(SCENE, {BOT}, ALIASES, (), nick), '这批有人泛泛聊 MBTI：不整批跳过')
only = [msg('846727248', '多多', '我都睡醒了主播怎么还在')]
check(gd.addressed_elsewhere_only(only, {BOT}, ALIASES, (), nick), '只有「主播怎么还在」：不插嘴')
check(not gd.addressed_elsewhere_only(only, {BOT}, ALIASES), '没配置称呼时不据此跳过（只标注）')
print('T2 对象判断 ✓')

# ============================================================ T3 群回合：称呼说明进入批次


class Stop(Exception):
    pass


class Session(dict):
    pass


class FakeService:
    database_resetting = False
    desktop_runtime_phase = 'running'
    rng = None
    config = {}
    runtime_config = {}

    def __init__(self):
        self.story = {'id': 'st', 'status': 'active', 'state': {},
                      'setting': {'timezone': 'Asia/Shanghai', 'character': {'name': '林小满'}}}
        self.buffered_group_turns, self.narrating_stories, self.group_willingness = {}, set(), {}
        self.captured, self.skips = None, []

    def now(self):
        from datetime import datetime, timezone
        return datetime(2026, 10, 5, 7, 25, 45, tzinfo=timezone.utc)

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
        return False

    async def sleep_resume_at(self, story, now):
        return None

    def semantic_turn_embedding_enabled(self):
        return False

    async def sticker_selection_for_session(self, *args):
        return {'mode': 'none', 'assets': [], 'groups': []}

    async def try_decide(self, story, participant, phase, from_, now, user_message, *args):
        self.captured = user_message
        raise Stop()

    def report_operation(self, *args):
        pass

    def report(self, *args):
        pass

    def note_group_skip_reason(self, group_id, reason, *args):
        self.skips.append(reason)
        return True


FakeService.flush_group_turn = chunk1.ServiceChunk1.flush_group_turn
real_gate = importlib.import_module(f'{PKG}.core.group_willingness').evaluate_willingness_gate
chunk1.evaluate_willingness_gate = lambda prev, *a, **k: real_gate(prev, *a[:4], {**a[4], 'random': 0.0}, **k)


def run(messages, rule_extra):
    svc = FakeService()
    svc.group_willingness['k'] = {'score': 1.0, 'updated_at': svc.now_ms()}
    rule = {'responseMode': 'always', 'willingness': {'enabled': True, 'threshold': 0.2, 'keywords': ['小满']}}
    rule.update(rule_extra)
    svc.buffered_group_turns['k'] = {
        'revision': 1, 'story_id': 'st', 'group_id': '992726871', 'rule': rule,
        'messages': [dict(m) for m in messages], 'mentioned_bot': False, 'quoted_bot': False, 'timer': None,
        'latest_session': Session(selfId=BOT, platform='onebot'), 'channel_id': '992726871',
    }
    try:
        asyncio.run(svc.flush_group_turn('k', 1))
    except Stop:
        pass
    return svc


svc = run(SCENE, {'member_nicknames': CONFIG})
um = svc.captured or ''
check(um.startswith('[群内称呼] 「主播」= 好小狗-放映手机'), um[:120])
check('我都睡醒了主播怎么还在\n（↑ 「主播」在这个群里指 好小狗-放映手机，不是你；这条不是在跟你说话）' in um, um)
svc = run(only, {'member_nicknames': CONFIG})
check(svc.captured is None and any('不是在找她' in s for s in svc.skips), '只有「主播怎么还在」：不调用模型')
svc = run(SCENE, {})
um = svc.captured or ''
check('[群内称呼]' not in um and '里面的「主播」说的是别人，不是你' in um, '没配置：通用称谓标注: %s' % um)
print('T3 群回合 ✓')

# ============================================================ T4 提示词
sysp = np_.system_prompt('user-message', None, None, '', '', '', group_turn=True)
check('TITLES ARE NOT HER' in sysp and 'not a streamer (主播)' in sysp and '谁是主播啦' in sysp and '[群内称呼]' in sysp,
      '称谓规则')
check('TITLES ARE NOT HER' not in np_.system_prompt('user-message', None, None, '', '', '', group_turn=False), '私聊不加')
print('T4 提示词 ✓')

# ============================================================ T5 频控窗口：取她自己的最近 20 条（不是全部条目里的 40 条）


def entry(i, kind, content):
    return {'id': i, 'kind': kind, 'content': content, 'occurredAt': '2026-10-05T07:%02d:%02d.000Z' % (i // 60, i % 60)}


# 复现 5389：她最近 10 条里 3 条笑着收尾，但都在 40 条之外夹在大量群消息之间
HER = [(100, '刚才忘记拍了啦！都换下一首了哈哈哈'), (300, '你们还真玩起来了哈哈哈'), (900, '刚才在专心打歌嘛'),
       (1000, '晚安啦！'), (1100, '干嘛啦！你不是说要睡了吗'), (1200, '前面在群里回过了呀！而且刚才打音游手根本停不下来哈哈哈'),
       (1300, '我们不本来就是好朋友嘛！快去睡觉啦'), (1400, '哪有啦 大家人都很好呀'), (1500, '晚安晚安！快去睡吧')]
ROWS = [entry(i, 'character-group-message' if i < 1000 else 'character-message', c) for i, c in HER]
ROWS += [entry(i, 'group-message', '群友在聊 MBTI %d' % i) for i in range(1501, 1600)]


class Host:
    db = object()
    runtime_config = {}

    async def db_get(self, table, query, options):
        rows = [r for r in ROWS if all(r.get(k) == v for k, v in query.items() if k != 'storyId')]
        rows.sort(key=lambda r: r['occurredAt'], reverse=True)
        return rows[:options['limit']]

    async def recent_entries(self, story_id, limit):
        rows = sorted(ROWS, key=lambda r: r['occurredAt'])
        return rows[-limit:]

    def report_operation(self, *args):
        self.logs.append(args[-1])


Host._her_recent_messages = chunk4.ServiceChunk4._her_recent_messages
Host._expression_guard = chunk4.ServiceChunk4._expression_guard
host = Host()
host.logs = []
mine = asyncio.run(host._her_recent_messages('st'))
check(len(mine) == 9 and mine[-1] == '晚安晚安！快去睡吧' and mine[0].startswith('刚才忘记拍了啦'), mine)
old_window = ex.her_messages(asyncio.run(host.recent_entries('st', 40)))
check(old_window == [], '旧做法：最近 40 条全是群消息，一条她的都看不到')
decision = {'groupReply': {'mode': 'immediate', 'content': '@职场霸凌所有人-多多 我在机厅玩呢！谁是主播啦哈哈哈'}}
asyncio.run(host._expression_guard({'id': 'st', 'state': {}}, 'user-message', decision))
check(decision['groupReply']['content'] == '@职场霸凌所有人-多多 我在机厅玩呢！谁是主播啦',
      '窗口 3/3 → 剪句尾笑声: %r %s' % (decision['groupReply']['content'], host.logs))
check(any('over-cap' in line for line in host.logs), host.logs)


class NoDb:
    async def recent_entries(self, story_id, limit):
        return sorted(ROWS, key=lambda r: r['occurredAt'])[-limit:]


NoDb._her_recent_messages = chunk4.ServiceChunk4._her_recent_messages
check(len(asyncio.run(NoDb()._her_recent_messages('st'))) == 9, '没有数据库句柄时退回 recent_entries(200)')
budget = vitality.request_context({'setting': {'timezone': 'Asia/Shanghai'}}, {}, 'user-message',
                                  __import__('datetime').datetime(2026, 10, 5, 7, 26, tzinfo=__import__('datetime').timezone.utc),
                                  [], None, mine).get('expressionBudget') or {}
check(budget.get('closer') == 'vary', '软提示也用她自己的消息: %s' % budget)
print('T5 频控窗口 ✓')

print(f'test_nickname_audience: {ok} checks passed')
