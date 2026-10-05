"""对象识别（别人 @ 别人时不插嘴、不认领称呼）与结尾口癖（句尾「哈哈哈」）测试。

用法：PYTHONPATH=<插件父目录> python test_audience_closer.py <插件包名>
"""

import asyncio
import importlib
import json
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
SEP = '<sep/>'
BOT = '1690619901'
ALIASES = ['林小满', '小满']
ok = 0


def check(cond, msg):
    global ok
    if not cond:
        raise AssertionError(msg)
    ok += 1


# 2026-10-04 20:26 现场（id 4535–4536）：她把「主播」当成在叫自己
SCENE = [
    {'senderId': '5005', 'senderName': '安纳金走天家长', 'speaker': '群成员「安纳金走天家长」',
     'content': '<at id="458593826"/>主播主播有空的话可以买个星战前线2玩玩'},
    {'senderId': '5005', 'senderName': '安纳金走天家长', 'speaker': '群成员「安纳金走天家长」', 'content': '全是梗'},
    {'senderId': '5005', 'senderName': '安纳金走天家长', 'speaker': '群成员「安纳金走天家长」', 'content': '<img src="a.png"/>'},
]

# T1 适配层（at 标签带显示名）在 tests/test_astrbot_bridge.py::test_at_keeps_display_name（需要 AstrBot 桩）。

# ============================================================ T2 渲染与对象判断
names = gd.name_map(SCENE + [{'content': '<at id="458593826" name="好小狗-放映手机"/>'}])
check(names.get('458593826') == '好小狗-放映手机', 'at 标签里的名字进入昵称表: %s' % names)
check(gd.render_mentions('<at id="458593826" name="好小狗-放映手机"/>主播', {}, {BOT}, '林小满') == '@好小狗-放映手机 主播',
      '标签自带名字')
check(gd.render_mentions('<at id="458593826"/>主播', {}, {BOT}, '林小满') == '@其他群友 主播', '不认识时是「其他群友」')

aud = gd.message_audiences(SCENE, {BOT}, ALIASES)
check(aud == ['other:458593826'] * 3, '同一人的补充「全是梗」与图片沿用对象: %s' % aud)
check(gd.addressed_elsewhere_only(SCENE, {BOT}, ALIASES), '整批对别人说')
note = gd.audience_note(aud[1], names)
check('对 @好小狗-放映手机 说的，不是对你' in note and '称呼都指对方' in note, note)
check(gd.audience_note('her', names) == '（↑ 这条是在找你）' and gd.audience_note(None, names) == '', '找她 / 无对象')

mixed = SCENE + [{'senderId': '6006', 'senderName': '路人', 'content': '这游戏还有人玩吗'}]
check(not gd.addressed_elsewhere_only(mixed, {BOT}, ALIASES), '有人泛泛对全群说：不跳过')
check(gd.message_audiences(mixed, {BOT}, ALIASES)[-1] is None, '泛泛的消息没有对象')
called = SCENE + [{'senderId': '6006', 'content': '小满你玩过没'}]
check(not gd.addressed_elsewhere_only(called, {BOT}, ALIASES), '提到她的名字：不跳过')
check(gd.message_audiences(called, {BOT}, ALIASES)[-1] == 'her', '提到名字 → her')
at_her = [{'senderId': '5005', 'content': '<at id="458593826"/><at id="%s"/>你们俩一起玩' % BOT}]
check(gd.message_audiences(at_her, {BOT}, ALIASES) == ['her'], '同时 @ 她和别人 → her')
quote_her = [{'senderId': '5005', 'content': '真的假的', 'quote': {'senderId': BOT, 'content': 'x'}}]
check(gd.message_audiences(quote_her, {BOT}, ALIASES) == ['her'], '引用她 → her')
quote_other = [{'senderId': '5005', 'content': '真的假的', 'quote': {'senderId': '7007', 'content': 'x'}}]
check(gd.addressed_elsewhere_only(quote_other, {BOT}, ALIASES), '引用别人 → 对别人说')
self_quote = [{'senderId': '5005', 'content': '补充一下', 'quote': {'senderId': '5005', 'content': 'x'}}]
check(not gd.addressed_elsewhere_only(self_quote, {BOT}, ALIASES), '引用自己不算对别人说')
check(not gd.addressed_elsewhere_only([SCENE[2]], {BOT}, ALIASES), '只有图片：不据此跳过')
check(not gd.addressed_elsewhere_only([{'senderId': '1', 'content': '<at id="all"/>开会'}], {BOT}, ALIASES), '@全体：不跳过')
prior = [{'senderId': '5005', 'content': '<at id="458593826"/>主播在吗'}]
check(gd.message_audiences([SCENE[1]], {BOT}, ALIASES, prior) == ['other:458593826'], '跨批次沿用对象')
check(gd.message_audiences([{'senderId': '5005', 'content': '<at id="458593826"/>主播'},
                            {'senderId': '5005', 'content': '小满也来'}], {BOT}, ALIASES)[1] == 'her', '后一条转向她')
check(chunk1._addressing_aliases({'setting': {'character': {'name': '林小满'}}},
                                 {'willingness': {'keywords': ['小满', '满满']}}) == ['林小满', '小满', '满满'], '称呼别名')
print('T2 对象判断 ✓')

# ============================================================ T3 群回合：整批对别人说时不调用模型；否则逐条标注


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
        self.captured, self.skips, self.reports = None, [], []

    def now(self):
        from datetime import datetime, timezone
        return datetime(2026, 10, 4, 12, 26, 8, tzinfo=timezone.utc)

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
        self.reports.append(args[4] % args[5:] if len(args) > 5 else args[4])

    def report(self, *args):
        pass

    def note_group_skip_reason(self, group_id, reason, *args):
        self.skips.append(reason)
        return True


FakeService.flush_group_turn = chunk1.ServiceChunk1.flush_group_turn


def run(messages, mentioned=False):
    svc = FakeService()
    svc.buffered_group_turns['k'] = {
        'revision': 1, 'story_id': 'st', 'group_id': '992726871',
        'rule': {'responseMode': 'always', 'willingnessPreset': 'custom',
                 'willingness': {'threshold': 0.0, 'keywords': ['小满']}},
        'messages': [dict(m) for m in messages], 'mentioned_bot': mentioned, 'quoted_bot': False, 'timer': None,
        'latest_session': Session(selfId=BOT, platform='onebot'), 'channel_id': '992726871',
    }
    try:
        asyncio.run(svc.flush_group_turn('k', 1))
    except Stop:
        pass
    return svc


svc = run(SCENE)
if svc.captured is None and not any('别的群友' in s for s in svc.skips):
    # 意愿门这一层没放行（与本测试无关）：直接把意愿门桩成放行再跑
    chunk1.evaluate_willingness_gate = lambda *a, **k: {
        'should_call': True, 'state': {'score': 0.924}, 'probability': 1.0, 'reason': 'threshold'}
    svc = run(SCENE)
check(svc.captured is None, '整批对别人说：不调用模型')
check(any('不是在找她' in s for s in svc.skips) and any('不插嘴' in r for r in svc.reports), (svc.skips, svc.reports))
svc = run(mixed)
um = svc.captured or ''
check('（↑ 这条是对 @其他群友 说的，不是对你；里面的称呼都指对方）' in um, um)
check(um.count('不是对你') == 3 and '这游戏还有人玩吗' in um, '逐条标注，泛泛那条不标: %s' % um)
svc = run(SCENE, mentioned=True)
check(svc.captured is not None, '被 @（mentioned_bot）时不跳过')
print('T3 群回合 ✓')

# ============================================================ T3b 群级开关 respond_to_mentions（配了 false 的群不再被 @ 强制唤醒）
check(chunk1._respond_to_mentions({}) is True and chunk1._respond_to_mentions({'respond_to_mentions': True}) is True,
      '缺省 / true：照常响应')
check(chunk1._respond_to_mentions({'respond_to_mentions': False}) is False
      and chunk1._respond_to_mentions({'respondToMentions': 'false'}) is False, 'false（两种拼写）')

real_gate = importlib.import_module(f'{PKG}.core.group_willingness').evaluate_willingness_gate
seen = []


def spy_gate(previous, preset, auto_map, life_status, legacy, payload, rng=None):
    seen.append((legacy, payload))
    return real_gate(previous, preset, auto_map, life_status, legacy, {**payload, 'random': 0.99}, rng=rng)


chunk1.evaluate_willingness_gate = spy_gate
AT_HER = [{'senderId': '5005', 'senderName': '群友', 'speaker': '群成员「群友」', 'content': '<at id="%s"/>小满在吗' % BOT}]


def run_rule(messages, rule_extra, mentioned=True):
    svc = FakeService()
    rule = {'responseMode': 'always', 'willingness': {'enabled': True, 'threshold': 0.2, 'base_gain': 0.16,
                                                     'quote_gain': 0.12, 'keyword_gain': 1.0, 'keywords': ['小满', '林小满']}}
    rule.update(rule_extra)
    svc.rule_seen = rule
    svc.buffered_group_turns['k'] = {
        'revision': 1, 'story_id': 'st', 'group_id': 'g', 'rule': rule,
        'messages': [dict(m) for m in messages], 'mentioned_bot': mentioned, 'quoted_bot': mentioned, 'timer': None,
        'latest_session': Session(selfId=BOT, platform='onebot'), 'channel_id': 'g',
    }
    try:
        asyncio.run(svc.flush_group_turn('k', 1))
    except Stop:
        pass
    return svc


seen.clear()
svc = run_rule(AT_HER, {})                       # 缺省：原样
check(svc.captured is not None and seen[-1][1]['mentioned_bot'] is True, '缺省：@ 照常强制唤醒')
seen.clear()
svc = run_rule(AT_HER, {'respond_to_mentions': True})    # 显式 true（2026-10-05 992726871 恢复后的配置）
check(svc.captured is not None and seen[-1][1]['mentioned_bot'] is True and seen[-1][0]['keywords'] == ['小满', '林小满'],
      '显式 true：与缺省一致，@ 照常唤醒、关键词保留')
seen.clear()
svc = run_rule(AT_HER, {'respond_to_mentions': False})   # 配了 false 的群
legacy, payload = seen[-1]
check(svc.captured is None, 'respond_to_mentions=false：@ 她、意愿冷启动时不开口: %s' % svc.skips)
check(payload['mentioned_bot'] is False and payload['quoted_bot'] is False and legacy['keywords'] == [],
      '@ / 引用 / 叫名字都不再加权')
check(any('意愿' in x for x in svc.skips), '跳过原因是意愿没到: %s' % svc.skips)
check(svc.rule_seen['willingness']['keywords'] == ['小满', '林小满'], '不改动原配置')
# 意愿自然攒满时照样会开口（「自己想说话的时候说话」）
svc = FakeService()
svc.group_willingness['k'] = {'score': 1.0, 'updated_at': svc.now_ms()}
rule = {'responseMode': 'always', 'respond_to_mentions': False,
        'willingness': {'enabled': True, 'threshold': 0.2, 'probability_amplifier': 1.3, 'keywords': ['小满']}}
svc.buffered_group_turns['k'] = {
    'revision': 1, 'story_id': 'st', 'group_id': 'g', 'rule': rule,
    'messages': [{'senderId': '5005', 'speaker': '群友', 'content': '今天好热'}], 'mentioned_bot': False, 'quoted_bot': False,
    'timer': None, 'latest_session': Session(selfId=BOT, platform='onebot'), 'channel_id': 'g',
}
chunk1.evaluate_willingness_gate = lambda prev, *a, **k: real_gate(prev, *a[:4], {**a[4], 'random': 0.0}, **k)
try:
    asyncio.run(svc.flush_group_turn('k', 1))
except Stop:
    pass
check(svc.captured is not None, '意愿到了：照常开口')
chunk1.evaluate_willingness_gate = real_gate
print('T3b respond_to_mentions ✓')

# ============================================================ T4 结尾识别与剪除


def cfg():
    return json.loads(json.dumps(ex.DEFAULT_CONFIG))


cases = {
    '我哪知道啦哈哈哈': '我哪知道啦',
    '好的，哈哈': '好的',
    '这个哈哈哈哈！': '这个！',
    '真的假的hhhh': '真的假的',
    '你也太离谱了吧笑死我了': '你也太离谱了吧',
    '啦哈哈哈 (｡･ω･｡)': '啦 (｡･ω･｡)',
    '哈哈你真的好笨': '哈哈你真的好笨',
    '我在写卷子': '我在写卷子',
}
for raw, expected in cases.items():
    got = ex.strip_trailing_laugh(raw)
    check(got == expected, '%r → %r（期望 %r）' % (raw, got, expected))
check(ex.strip_trailing_laugh('哈哈哈哈') == '', '整个气泡都是笑声 → 空（由调用方决定是否保留）')
check(ex.ends_with_laugh('好耶哈哈哈！') and ex.ends_with_laugh('a' + SEP + '哈哈哈') and not ex.ends_with_laugh('哈哈好的'),
      'ends_with_laugh')
check(ex.ending_key('我哪知道啦哈哈哈哈哈') == '啦哈哈哈' and ex.ending_key('你等等我好不好啦') == '好不好啦', '结尾归一')
print('T4 结尾识别 ✓')

# ============================================================ T5 硬兜底（与开头问号对称）
# 2026-10-04 现场：最近 20 条里 16 条笑着收尾
REAL = ['好家伙你还真跑去查词典了啦哈哈哈', '我哪知道啦哈哈哈', '那你去买啊好哈哈哈', '这也能分析的吗哈哈哈',
        '少道德绑架我啦哈哈哈', '还打错字哈哈哈', '我日语全靠看番好不好啦哈哈哈', '我怎么就没有灵魂啦哈哈哈']
clean = ['好呀', '在写卷子', '晚安', '嗯嗯', '物理好难']


def decision(text):
    return {'script': '她回了一句：' + text.replace(SEP, '') + '\n\n然后放下手机。',
            'groupReply': {'mode': 'immediate', 'content': text}, 'group_reply': {'mode': 'immediate', 'content': text}}


d = decision('那也太离谱了吧哈哈哈')
check(ex.guard_decision(d, clean, None, SEP, cfg()) == [] and d['groupReply']['content'] == '那也太离谱了吧哈哈哈',
      '历史干净：偶尔笑一次放行')
d = decision('那也太离谱了吧哈哈哈')
logs = ex.guard_decision(d, clean + ['哈哈哈好吧'], None, SEP, cfg())
check(d['groupReply']['content'] == '那也太离谱了吧哈哈哈', '上一条笑在开头不算收尾')
d = decision('那也太离谱了吧哈哈哈')
logs = ex.guard_decision(d, clean + ['行吧哈哈'], None, SEP, cfg())
check(d['groupReply']['content'] == '那也太离谱了吧' == d['group_reply']['content'] and any('consecutive' in x for x in logs),
      logs)
check('她回了一句：那也太离谱了吧\n' in d['script'], '剧本同步: %r' % d['script'])
d = decision('那也太离谱了吧哈哈哈')
hist = ['a哈哈哈', '好', 'b哈哈', '嗯', 'c哈哈哈', '行']
logs = ex.guard_decision(d, hist, None, SEP, cfg())
check(d['groupReply']['content'] == '那也太离谱了吧' and any('over-cap' in x for x in logs), '窗口超标: %s' % logs)
d = decision('哈哈哈哈哈')
ex.guard_decision(d, REAL, None, SEP, cfg())
check(d['groupReply']['content'] == '哈哈哈哈哈', '整条只有笑声：保留（不发空消息）')
d = decision('你也太会了吧' + SEP + '哈哈哈哈')
ex.guard_decision(d, REAL, None, SEP, cfg())
check(d['groupReply']['content'] == '你也太会了吧', '只有笑声的气泡去掉')
d = decision('？？？我哪知道啦哈哈哈')
ex.guard_decision(d, REAL + ['？？好'], None, SEP, cfg())
check(d['groupReply']['content'] == '我哪知道啦', '开头问号与句尾笑声一起处理: %r' % d['groupReply']['content'])
off = cfg(); off['closer']['hard_guard'] = False
d = decision('我哪知道啦哈哈哈')
ex.guard_decision(d, REAL, None, SEP, off)
check(d['groupReply']['content'] == '我哪知道啦哈哈哈', 'closer.hard_guard 关闭')
print('T5 硬兜底 ✓')

# ============================================================ T6 裸 QQ 号
d = decision('@269502169 你说的那个我也看过')
logs = ex.guard_decision(d, clean, None, SEP, cfg())
check(d['groupReply']['content'] == '你说的那个我也看过' and any('裸 QQ 号' in x for x in logs), d)
d = decision('我考了 98 分，电话 12345 不算')
ex.guard_decision(d, clean, None, SEP, cfg())
check(d['groupReply']['content'] == '我考了 98 分，电话 12345 不算', '没有 @ 的数字不动')
nm = cfg(); nm['raw_mention_guard'] = False
d = decision('@269502169 好')
ex.guard_decision(d, clean, None, SEP, nm)
check(d['groupReply']['content'] == '@269502169 好', '开关')
print('T6 裸 QQ 号 ✓')

# ============================================================ T7 软反馈与提示词
budget = ex.prompt_budget(REAL, None, cfg())
check(budget.get('closer') == 'vary' and budget.get('avoidEndings', [])[0] == '啦哈哈哈', budget)
check('closer' not in ex.prompt_budget(clean, None, cfg()), '历史干净时没有 closer')
json.dump({'closer': {'max_in_window': 9}}, open(os.environ['HDSI_EXPRESSION_CONFIG'], 'w'))
check(ex.load_config()['closer']['hard_guard'] is True and ex.load_config()['closer']['max_in_window'] == 9, '热读取')
os.remove(os.environ['HDSI_EXPRESSION_CONFIG'])
sysp = np_.system_prompt('user-message', None, None, '', '', '', group_turn=True)
check('AUDIENCE:' in sysp and '主播' in sysp and 'Never write a raw QQ number' in sysp, '对象规则')
check('closer=vary' in sysp and 'avoidEndings' in sysp and '啦哈哈哈' in sysp, '结尾规则')
check('AUDIENCE:' not in np_.system_prompt('user-message', None, None, '', '', '', group_turn=False), '私聊不加对象规则')
print('T7 软反馈与提示词 ✓')

print(f'test_audience_closer: {ok} checks passed')
