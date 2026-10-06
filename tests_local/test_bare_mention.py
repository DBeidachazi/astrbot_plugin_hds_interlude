"""只有一个 @ 的群消息：照实呈现 + 往前 5 分钟的事实指针（不编「没说话」）。

用法：PYTHONPATH=<插件父目录> python test_bare_mention.py <插件包名>
"""

import asyncio
import importlib
import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone

PKG = sys.argv[1] if len(sys.argv) > 1 else 'hds_interlude'
tmp = tempfile.mkdtemp()
for name in ('EXPRESSION', 'LIFE_HOOKS', 'STORY_ARCS'):
    os.environ['HDSI_%s_CONFIG' % name] = os.path.join(tmp, name.lower() + '.json')

gd = importlib.import_module(f'{PKG}.core.group_digest')
chunk1 = importlib.import_module(f'{PKG}.core.service.chunk1')
BOT = '1690619901'
T0 = datetime(2026, 10, 6, 1, 0, tzinfo=timezone.utc)
ok = 0


def check(cond, msg):
    global ok
    if not cond:
        raise AssertionError(msg)
    ok += 1


def msg(sender, name, content, seconds, mid):
    return {'senderId': sender, 'senderName': name, 'speaker': '群成员「%s」' % name, 'content': content,
            'occurredAt': T0 + timedelta(seconds=seconds), 'messageId': mid}


AT_HER = '<at id="%s" name="林小满"/>' % BOT
ASK = msg('1921977730', '睡觉-人', '小满在干什么', 0, 'm1')
BARE = msg('1921977730', '睡觉-人', AT_HER, 70, 'm2')
OTHER_TALK = msg('3188754042', '挂科学-Cody', 'MBTI 全中了', 30, 'm3')
HER = msg(BOT, '林小满', '刚醒', 40, 'm4')

# ============================================================ T1 判定与时间线
check(gd.is_mention_only(BARE) and gd.is_mention_only({'content': AT_HER + ' <at id="2"/> '}), '只有 @')
check(not gd.is_mention_only(ASK) and not gd.is_mention_only({'content': AT_HER + '在吗'}), '带字的不是')
check(not gd.is_mention_only({'content': AT_HER + '<img src="x"/>'}), '带图的不是')
check(not gd.is_mention_only({'content': ''}), '空的不是')
timeline = gd.merged_timeline([ASK, OTHER_TALK, HER, BARE], [dict(BARE)])
check([item['messageId'] for item in timeline] == ['m1', 'm3', 'm4', 'm2'], '按时间合并、按 messageId 去重')
print('T1 判定 ✓')

# ============================================================ T2 事实指针
names = gd.name_map(timeline)
p = gd.mention_pointer(BARE, timeline, names, {BOT}, '林小满', ['林小满', '小满'])
check(p == '睡觉-人 1 分钟前发过「小满在干什么」', p)
quick = msg('1921977730', '睡觉-人', AT_HER, 20, 'm5')
check(gd.mention_pointer(quick, gd.merged_timeline([ASK], [quick]), names, {BOT}, '林小满') == '睡觉-人 刚刚发过「小满在干什么」',
      '一分钟内：刚刚')
# 别人 @ 她，前面是 A 在问她 → 指向 A 的话
by_b = msg('5005', '安纳金', AT_HER, 90, 'm6')
p = gd.mention_pointer(by_b, gd.merged_timeline([ASK, OTHER_TALK], [by_b]), names, {BOT}, '林小满', ['小满'])
check(p == '睡觉-人 1 分钟前发过「小满在干什么」', '别人的问题提到她: %s' % p)
# 同一个人优先于别人提到她的话
mine = msg('5005', '安纳金', '今天好无聊', 80, 'm7')
p = gd.mention_pointer(by_b, gd.merged_timeline([ASK, mine], [by_b]), names, {BOT}, '林小满', ['小满'])
check(p == '安纳金 刚刚发过「今天好无聊」', p)
# 前面没有相关的话：不加（保持纯 @ 事实）
lonely = msg('1921977730', '睡觉-人', AT_HER, 70, 'm8')
check(gd.mention_pointer(lonely, gd.merged_timeline([OTHER_TALK, HER], [lonely]), names, {BOT}, '林小满', ['小满']) == '',
      '别人聊别的、她自己的话都不算')
old = msg('1921977730', '睡觉-人', '小满在干什么', -400, 'm9')
check(gd.mention_pointer(lonely, gd.merged_timeline([old], [lonely]), names, {BOT}, '林小满', ['小满']) == '', '超过 5 分钟不算')
prev_bare = msg('1921977730', '睡觉-人', AT_HER, 10, 'm10')
pic = msg('1921977730', '睡觉-人', '[图片]', 20, 'm11')
check(gd.mention_pointer(lonely, gd.merged_timeline([prev_bare, pic], [lonely]), names, {BOT}, '林小满') == '',
      '前面也只是 @ 或图片：不算')
later = msg('1921977730', '睡觉-人', '你在干嘛', 80, 'm12')
check(gd.mention_pointer(BARE, gd.merged_timeline([later], [BARE, later]), names, {BOT}, '林小满') == '',
      '只往前看（@ 在前、字在后的情况由同一批次的顺序呈现）')
print('T2 事实指针 ✓')

# ============================================================ T3 群回合里的呈现


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

    def __init__(self, context):
        self.story = {'id': 'st', 'status': 'active', 'state': {},
                      'setting': {'timezone': 'Asia/Shanghai', 'character': {'name': '林小满'}}}
        self.buffered_group_turns, self.narrating_stories, self.group_willingness = {}, set(), {}
        self.context, self.captured = context, None

    def now(self):
        return T0 + timedelta(seconds=75)

    def now_ms(self):
        return int(self.now().timestamp() * 1000)

    async def get_story(self, story_id):
        return self.story

    async def serial(self, story_id, task):
        return await task()

    async def group_messages(self, story_id, group_id, limit):
        return [{'sender_id': m['senderId'], 'sender_name': m['senderName'], 'speaker': m['speaker'],
                 'content': m['content'], 'occurred_at': m['occurredAt'].isoformat(), 'message_id': m['messageId'],
                 'direction': 'user'} for m in self.context]

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

    def note_group_skip_reason(self, *args):
        return True


FakeService.flush_group_turn = chunk1.ServiceChunk1.flush_group_turn


def run(context, batch):
    svc = FakeService(context)
    svc.buffered_group_turns['k'] = {
        'revision': 1, 'story_id': 'st', 'group_id': '992726871',
        'rule': {'responseMode': 'always', 'willingness': {'keywords': ['小满']}},
        'messages': [dict(m) for m in batch], 'mentioned_bot': True, 'quoted_bot': False, 'timer': None,
        'latest_session': Session(selfId=BOT, platform='onebot'), 'channel_id': '992726871',
    }
    try:
        asyncio.run(svc.flush_group_turn('k', 1))
    except Stop:
        pass
    return svc.captured or ''


# 问题在上一批（已在群上下文里），这一批只有 @
um = run([ASK, OTHER_TALK, BARE], [BARE])
check('[群聊连续消息 1｜群成员「睡觉-人」]\n@林小满\n（↑ 这条是在找你；睡觉-人 1 分钟前发过「小满在干什么」）' in um, um)
check('没说话' not in um, '绝不编「没说话」')
# 问题和 @ 在同一批：两条按顺序都在
um = run([ASK, BARE], [ASK, BARE])
check(um.index('小满在干什么') < um.index('@林小满') and '1 分钟前发过「小满在干什么」' in um, um)
# 前面没有相关的话：只有事实
um = run([OTHER_TALK, lonely], [lonely])
check('@林小满\n（↑ 这条是在找你）' in um and '发过「' not in um, um)
print('T3 群回合呈现 ✓')

print(f'test_bare_mention: {ok} checks passed')
