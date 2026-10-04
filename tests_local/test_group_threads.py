"""群聊多人多话题：@ 渲染、批次摘要、引用回复能力、开场白口癖 测试。

用法：PYTHONPATH=<插件父目录> python test_group_threads.py <插件包名>
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
chunk2 = importlib.import_module(f'{PKG}.core.service.chunk2')
ok = 0
BOT = '1690619901'


def check(cond, msg):
    global ok
    if not cond:
        raise AssertionError(msg)
    ok += 1


# 2026-10-04 现场的那一批（id 4228–4231）
BATCH = [
    {'senderId': '1001', 'senderName': '睡觉-人', 'speaker': '群成员「睡觉-人」（QQ：1001）',
     'content': '你活着就是为了按f和弦吗', 'messageId': '695420469'},
    {'senderId': '1001', 'senderName': '睡觉-人', 'speaker': '群成员「睡觉-人」（QQ：1001）',
     'content': '<at id="1690619901"/>笑死我了', 'messageId': '498964640'},
    {'senderId': '1001', 'senderName': '睡觉-人', 'speaker': '群成员「睡觉-人」（QQ：1001）',
     'content': '<at id="1690619901"/>你活着就是为了按f和弦吗', 'messageId': '751314995',
     'quote': {'senderId': BOT, 'senderName': '林小满', 'content': '？不打！我连F和弦都没搞定呢，哪有空打游戏啦'}},
    {'senderId': '2002', 'senderName': '美术-匙老师', 'speaker': '群成员「美术-匙老师」（QQ：2002）',
     'content': '给我介绍一下题瓦特大陆的情况<at id="1690619901"/>', 'messageId': '1474090067'},
]

# ============================================================ T1 @ 渲染
names = gd.name_map(BATCH + [{'senderId': '3003', 'senderName': '若离'}])
check(names == {'1001': '睡觉-人', '2002': '美术-匙老师', BOT: '林小满', '3003': '若离'}, names)
cases = {
    '<at id="1690619901"/>笑死我了': '@林小满 笑死我了',
    "给我介绍一下<at id='1690619901'/>": '给我介绍一下@林小满',
    '<at id=1690619901>在吗</at>': '@林小满 在吗',
    '<at id="3003"/> 你也来': '@若离 你也来',
    '<at id="99999"/>是谁': '@其他群友 是谁',
    '<AT ID="all"/>开会了': '@全体成员 开会了',
    '没有艾特的普通消息': '没有艾特的普通消息',
}
for raw, expected in cases.items():
    got = gd.render_mentions(raw, names, {BOT}, '林小满')
    check(got == expected, '%r → %r（期望 %r）' % (raw, got, expected))
check('1690619901' not in gd.render_mentions(BATCH[3]['content'], names, {BOT}, '林小满'), '不再出现裸 QQ 号')
print('T1 @ 渲染 ✓')

# ============================================================ T2 批次摘要
digest = gd.batch_digest(BATCH, {BOT}, '林小满', names)
check(digest and digest.startswith('[本批概况] 这一批共 4 条，来自 2 位群友'), digest)
check('· 睡觉-人：3 条，其中 2 条 @ 了你，引用了你说的「？不打！我连F和弦都没搞定呢，哪有空打游戏啦」' in digest, digest)
check('· 美术-匙老师：1 条，其中 1 条 @ 了你；最后一句：「给我介绍一下题瓦特大陆的情况@林小满」' in digest, digest)
check('不要揉成一句' in digest and '正面回应' in digest, '摘要带处理原则')
check(digest.index('睡觉-人') < digest.index('美术-匙老师'), '按首次出现排序')
check(gd.batch_digest(BATCH[:3], {BOT}, '林小满', names) is None, '只有一位发言人时不生成摘要')
check(gd.batch_digest([], {BOT}, '林小满') is None, '空批次')
own = BATCH + [{'senderId': BOT, 'senderName': '林小满', 'content': '我自己'}]
check(gd.batch_digest(own, {BOT}, '林小满', names).count('·') == 2, '她自己的消息不计入')
longer = [{'senderId': '1', 'senderName': 'A', 'content': '长' * 80}, {'senderId': '2', 'senderName': 'B', 'content': 'b'}]
check('…」' in gd.batch_digest(longer, {BOT}, '林小满'), '长消息截断')
print('T2 批次摘要 ✓')

# ============================================================ T3 接入群回合（真实 flush_group_turn，桩掉模型调用）


class Captured(Exception):
    pass


chunk1.evaluate_group_willingness = lambda previous, config, payload, rng=None: {
    'should_call': True, 'state': {'score': 1.0}, 'probability': 1.0, 'reason': 'forced-mention'}


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
        self.captured = None

    def now(self):
        from datetime import datetime, timezone
        return datetime(2026, 10, 4, 10, 48, 10, tzinfo=timezone.utc)

    def now_ms(self):
        return int(self.now().timestamp() * 1000)

    async def get_story(self, story_id):
        return self.story

    async def serial(self, story_id, task):
        return await task()

    async def group_messages(self, story_id, group_id, limit):
        return [{'sender_id': '3003', 'sender_name': '若离', 'speaker': '群成员「若离」', 'content': '<at id="1001"/>别闹',
                 'occurred_at': '2026-10-04T10:46:00Z', 'direction': 'user', 'message_id': '1', 'message_ref': 'msg-1'}]

    def group_chat_capabilities(self, session, messages):
        return None

    async def group_cooldown_active(self, *args):
        return False

    async def sleep_resume_at(self, story, now):
        return None

    def semantic_turn_embedding_enabled(self):
        return False

    async def sticker_catalog_for_session(self, *args):
        return []

    async def sticker_selection_for_session(self, *args):
        return {'mode': 'none', 'assets': [], 'groups': []}

    async def try_decide(self, story, participant, phase, from_, now, user_message, *args):
        self.captured = {'user_message': user_message, 'group_context': args[2]}
        raise Captured()

    def report_operation(self, *args):
        pass

    def report(self, *args):
        pass

    def note_group_skip_reason(self, *args):
        return True


FakeService.flush_group_turn = chunk1.ServiceChunk1.flush_group_turn
svc = FakeService()
svc.buffered_group_turns['k'] = {
    'revision': 1, 'story_id': 'st', 'group_id': '992726871', 'rule': {'willingness': {}},
    'messages': [dict(m) for m in BATCH], 'mentioned_bot': True, 'quoted_bot': True, 'timer': None,
    'latest_session': Session(selfId=BOT, platform='onebot'), 'channel_id': '992726871',
}
try:
    asyncio.run(svc.flush_group_turn('k', 1))
except Captured:
    pass
cap = svc.captured
check(cap is not None, 'try_decide 应被调用')
um = cap['user_message']
check(um.startswith('[本批概况]') and '[群聊连续消息 4｜群成员「美术-匙老师」（QQ：2002）]' in um, um[:200])
check('<at' not in um and '@林小满 笑死我了' in um, '批次里的 @ 已渲染')
ctx_msgs = cap['group_context']['messages']
check(ctx_msgs[0]['content'] == '@睡觉-人 别闹', '群上下文里的 @ 也渲染（用批次里的昵称映射）: %r' % ctx_msgs[0]['content'])
check(BATCH[1]['content'] == '<at id="1690619901"/>笑死我了', '不改动原始消息对象')
print('T3 群回合接入 ✓')

# ============================================================ T4 引用回复能力（方案 ①）


class CapService:
    def __init__(self, chat_actions):
        self.config = {'chat_actions': chat_actions}
        self.transport = object()


CapService.group_chat_capabilities = chunk2.ServiceChunk2.group_chat_capabilities
msgs = [{'messageRef': 'msg-1', 'messageId': '1'}]
session = {'platform': 'onebot'}
old_cfg = {'enabled': False, 'platforms': ['qq'], 'quote_reply': True, 'message_reactions': True, 'native_faces': True}
new_cfg = {'enabled': True, 'platforms': ['qq'], 'quote_reply': True, 'message_reactions': False, 'native_faces': False,
           'allowed_reactions': ['like'], 'allowed_native_faces': ['smile']}
check(CapService(old_cfg).group_chat_capabilities(session, msgs) is None, '旧配置：不提供任何能力')
caps = CapService(new_cfg).group_chat_capabilities(session, msgs)
check(caps and (caps.get('quoteReply') or caps.get('quote_reply')) is True, caps)
check(not (caps.get('reactions') or caps.get('allowedReactions')) and not (caps.get('nativeFaces') or caps.get('native_faces')),
      '只开引用，表态与原生表情保持关闭: %s' % caps)
wire = chunk1._chat_capabilities_wire(caps)
prompt_line = np_.chat_action_instruction(wire)
check('replyTo' in prompt_line, '提示词提供 replyTo 引用说明')
print('T4 引用回复能力 ✓')

# ============================================================ T5 提示词
group_p = np_.system_prompt('user-message', None, None, '', '', '', group_turn=True)
private_p = np_.system_prompt('user-message', None, None, '', '', '')
check('GROUP THREADS' in group_p and 'GROUP THREADS' not in private_p, '多线程规则只在群聊回合')
check('never blend' in group_p and 'not just a reaction to a typo' in group_p, '规则要点')
check('opener=vary' in group_p, '开场白规则')
print('T5 提示词 ✓')

# ============================================================ T6 开场白口癖（方案 ③）
q = ['？？？你在说什么', '？不打', '？？这话题跨度也太大了', '？？？都说了我是活人', '好呀', '嗯']
check(ex.opener_hint(q) == 'vary', '4/10 以「？」开头 → vary')
check(ex.opener_hint(q[1:]) is None, '3 条时不提示')
check(ex.opener_hint(['?? what', '? ok', '?嗯', '?啊']) == 'vary', '半角问号也算')
check(ex.opener_hint(['好的？', '真的吗？', '你呢？', '为什么？']) is None, '句尾问号不算开头')
budget = ex.prompt_budget(q)
check(budget.get('opener') == 'vary' and budget.get('kaomoji'), budget)
import json  # noqa: E402
json.dump({'opener': {'enabled': False}}, open(os.environ['HDSI_EXPRESSION_CONFIG'], 'w'))
check(ex.opener_hint(q) is None and 'opener' not in ex.prompt_budget(q), '可关闭')
json.dump({'opener': {'max_in_window': 2}}, open(os.environ['HDSI_EXPRESSION_CONFIG'], 'w'))
os.utime(os.environ['HDSI_EXPRESSION_CONFIG'], (5, 6))
check(ex.opener_hint(['？a', '？b', 'c']) == 'vary', '上限可配')
os.remove(os.environ['HDSI_EXPRESSION_CONFIG'])
print('T6 开场白口癖 ✓')

print(f'test_group_threads: {ok} checks passed')
