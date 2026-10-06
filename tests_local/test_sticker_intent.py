"""两级表情选择：意图传给第二步、明确索要时不静默 null、只能发库里的、没发出去要补事实、INFO 日志。

复现 2026-10-06 16:02：用户「你有表情包可以发吗」，第一步三次都点了分组，第二步只看到正文「这就给你发」
→ null，剧本里却写「点击了发送」。

用法：PYTHONPATH=<插件父目录> python test_sticker_intent.py <插件包名>
"""

import asyncio
import importlib
import sys
from datetime import datetime, timezone

PKG = sys.argv[1] if len(sys.argv) > 1 else 'hds_interlude'
helpers = importlib.import_module(f'{PKG}.core.service.helpers')
chunk2 = importlib.import_module(f'{PKG}.core.service.chunk2')
np_ = importlib.import_module(f'{PKG}.core.narrator_prompts')
narrator_mod = importlib.import_module(f'{PKG}.core.narrator')
NOW = datetime(2026, 10, 6, 8, 3, tzinfo=timezone.utc)
ok = 0


def check(cond, msg):
    global ok
    if not cond:
        raise AssertionError(msg)
    ok += 1


CATALOG = [
    {'assetId': 'cat-laugh', 'group': '动物表情', 'description': '张大嘴狂笑的白猫，指着对方嘲笑', 'status': 'active'},
    {'assetId': 'cat-pounce', 'group': '动物表情', 'description': '黑猫被另一只黑猫扑倒偷袭', 'status': 'active'},
    {'assetId': 'panda', 'group': '魔性搞怪', 'description': '龙袍熊猫头狂笑', 'status': 'active'},
]


def decision(want='他点名要表情包，想发张猫猫的'):
    media = {'stickerGroupId': '动物表情', 'placement': 'after-text', 'willingness': 0.9}
    if want:
        media['want'] = want
    return {'interaction': {'reply': {'mode': 'immediate', 'content': '这就给你发'}}, 'localMedia': media}


# ============================================================ T1 意图解析与提示词
check(helpers.parse_sticker_intent(decision()) == '他点名要表情包，想发张猫猫的', '读 localMedia.want')
check(helpers.parse_sticker_intent({'local_media': {'intent': ' 逗他 '}}) == '逗他', 'snake_case / intent 别名')
check(helpers.parse_sticker_intent({}) == '' and helpers.parse_sticker_intent({'localMedia': 'x'}) == '', '没有')
check(helpers.parse_sticker_selection_receipt({'stickerAssetId': None, 'reason': '都不贴'})['reason'] == '都不贴', '回执带 reason')
groups_text = np_.sticker_instruction(None, 0.7, [{'groupId': '动物表情', 'name': '动物表情', 'count': 2}])
check('"want"' in groups_text and 'explicitly asks her for a sticker' in groups_text, '第一步：want 字段 + 被点名要时就点')
check('her phone album' in groups_text and 'never write that a sticker or picture was sent' in groups_text, '只能发库里的、不写已发送')
inline_text = np_.sticker_instruction([{'assetId': 'a'}], 0.7)
check('her phone album' in inline_text, '平铺目录也有同一条')
sel = np_.sticker_selection_instruction(0.7)
check('intent' in sel and 'userMessage' in sel and 'closest candidate' in sel and '"reason"' in sel, '第二步放宽并要理由')
print('T1 意图与提示词 ✓')

# ============================================================ T2 第二步：意图与对方原话真的交过去；挑中就发


class Narrator:
    def __init__(self, receipt):
        self.receipt, self.calls = receipt, []

    async def select_sticker(self, items, message, threshold, group_id, intent='', user_message=''):
        self.calls.append({'items': items, 'message': message, 'group': group_id, 'intent': intent, 'user': user_message})
        return self.receipt


class OldNarrator:
    def __init__(self, receipt):
        self.receipt, self.calls = receipt, 0

    async def select_sticker(self, items, message, threshold, group_id):
        self.calls += 1
        return self.receipt


class Host:
    expression_threshold = 0.7

    def __init__(self, narrator):
        self.narrator = narrator
        self.sticker_catalog = CATALOG
        self.sticker_by_id = {item['assetId']: item for item in CATALOG}
        self.logs, self.entries = [], []

    def report_standalone_operation(self, level, severity, message, *args):
        self.logs.append((severity, message % args if args else message))

    def report_standalone(self, *args):
        self.logs.append(('warn', str(args)))

    async def serial(self, story_id, task):
        return await task()

    async def append_entry(self, story_id, entry, now, participant_id=''):
        self.entries.append((participant_id, entry))


for name in ('resolve_sticker_selection', 'request_sticker_selection', 'resolve_sticker', '_note_sticker_miss',
             'record_sticker_miss', '_report_sticker_selection_fallback'):
    setattr(Host, name, getattr(chunk2.ServiceChunk2, name))
SELECTION = {'mode': 'groups', 'assets': [], 'groups': [{'groupId': '动物表情'}]}


def run(host, dec, user='你有表情包可以发吗'):
    budget = {'userText': user}
    sticker = asyncio.run(host.resolve_sticker_selection(dec, SELECTION, budget))
    return sticker, budget


host = Host(Narrator({'stickerAssetId': 'cat-laugh', 'willingness': 0.85, 'content': '这就给你发'}))
sticker, budget = run(host, decision())
call = host.narrator.calls[0]
check(call['intent'] == '他点名要表情包，想发张猫猫的' and call['user'] == '你有表情包可以发吗', '意图与对方原话交给第二步: %s' % call)
check(call['group'] == '动物表情' and [i['assetId'] for i in call['items']] == ['cat-laugh', 'cat-pounce'], '只给该组候选')
check(sticker and sticker['assetId'] == 'cat-laugh' and 'miss' not in budget, '挑中就发，不记 miss')
d = decision()
run(Host(Narrator({'stickerAssetId': 'cat-laugh', 'willingness': 0.85, 'content': 'x'})), d)
check(d['localMedia']['assetId'] == 'cat-laugh', '选中的 assetId 写回草稿（账本认它）')
old = Host(OldNarrator({'stickerAssetId': 'cat-pounce', 'willingness': 0.8, 'content': 'x'}))
sticker, _ = run(old, decision())
check(sticker and sticker['assetId'] == 'cat-pounce' and old.narrator.calls == 1, '旧签名的实现照样能挑')
print('T2 意图传递 ✓')

# ============================================================ T3 没发出去：INFO 日志 + 原因


def miss_of(receipt, dec=None, catalog=None):
    host = Host(Narrator(receipt))
    if catalog is not None:
        host.sticker_catalog = catalog
    sticker, budget = run(host, dec or decision())
    infos = [m for s, m in host.logs if s == 'info']
    return sticker, budget.get('miss'), infos, host


sticker, miss, infos, _ = miss_of({'stickerAssetId': None, 'willingness': 0.0, 'content': '这就给你发', 'reason': '没有煤球的照片'})
check(sticker is None and miss['reason'] == '模型认为候选都不贴切：没有煤球的照片' and miss['candidates'] == 2, miss)
check(infos and '表情选择未发出 分组=动物表情 候选=2 原因=模型认为候选都不贴切：没有煤球的照片 意图=他点名要表情包' in infos[0], infos)
sticker, miss, _, _ = miss_of({'stickerAssetId': 'cat-laugh', 'willingness': 0.3, 'content': 'x'})
check(sticker is None and miss['reason'] == '意愿 0.30 未达阈值 0.70', miss)
sticker, miss, _, _ = miss_of({'stickerAssetId': 'panda', 'willingness': 0.9, 'content': 'x'})
check(sticker is None and '不在候选里' in miss['reason'], '别的组的素材不能借来发: %s' % miss)
sticker, miss, _, _ = miss_of(None)
check(sticker is None and '没有可用回执' in miss['reason'], miss)
sticker, miss, _, _ = miss_of({'stickerAssetId': 'x', 'willingness': 1}, catalog=[])
check(sticker is None and miss['candidates'] == 0 and '分组不存在' in miss['reason'], miss)
_, miss, infos, _ = miss_of({'stickerAssetId': None, 'content': 'x'}, dec=decision(want=''))
check(miss['intent'] == '' and '意图=（未给）' in infos[0], '没给意图也照样记')
# 没点分组（第一步本来就不想发）：不算 miss
host = Host(Narrator(None))
sticker, budget = run(host, {'interaction': {'reply': {'mode': 'immediate', 'content': '好'}}})
check(sticker is None and 'miss' not in budget and host.narrator.calls == [], '没点分组：不追问、不记')
print('T3 未发出与日志 ✓')

# ============================================================ T4 补事实：下一回合知道其实没发
_, budget = run(host2 := Host(Narrator({'stickerAssetId': None, 'reason': '没有煤球的照片', 'content': 'x'})), decision())
asyncio.run(host2.record_sticker_miss('st', 'onebot:1:269502169', budget, NOW))
pid, entry = host2.entries[0]
check(pid == 'onebot:1:269502169' and entry['kind'] == 'system', entry)
check(entry['content'] == '（她想发一张表情包（分组：动物表情）但没挑到合适的，实际没有发出去。原因：模型认为候选都不贴切：没有煤球的照片）',
      entry['content'])
check(entry['metadata']['stickerOutcome'] == 'not-sent' and entry['metadata']['intent'], entry['metadata'])
host3 = Host(Narrator(None))
asyncio.run(host3.record_sticker_miss('st', 'p', {}, NOW))
check(host3.entries == [], '没有 miss：不补')
src = open(importlib.import_module(f'{PKG}.core.service.chunk3').__file__, encoding='utf-8').read()
check("sticker_follow_up: dict[str, Any] = {'userText': user_message}" in src and 'record_sticker_miss(story_id' in src, '私聊接线')
src1 = open(importlib.import_module(f'{PKG}.core.service.chunk1').__file__, encoding='utf-8').read()
check("sticker_follow_up: dict[str, Any] = {'userText': user_message}" in src1 and "record_sticker_miss(pick(story, 'id'), ''" in src1, '群聊接线')
# SilentNarrator 也接受新参数
check(asyncio.run(narrator_mod.SilentNarrator().select_sticker([], 'x', 0.7, 'g', intent='i', user_message='u')) is None,
      'SilentNarrator 签名一致')
print('T4 补事实 ✓')

print(f'test_sticker_intent: {ok} checks passed')
