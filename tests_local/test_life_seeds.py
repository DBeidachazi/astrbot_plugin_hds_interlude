"""聊天种子（Life Seeds）+ 世界事件并入生活钩子 测试。

用法：PYTHONPATH=<插件父目录> python test_life_seeds.py <插件包名>
"""

import asyncio
import importlib
import json
import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

PKG = sys.argv[1] if len(sys.argv) > 1 else 'hds_interlude'
tmp = tempfile.mkdtemp()
os.environ['HDSI_LIFE_HOOKS_CONFIG'] = os.path.join(tmp, 'life_hooks.json')   # 不存在 → 默认库（合并模式开）
os.environ['HDSI_STORY_ARCS_CONFIG'] = os.path.join(tmp, 'story_arcs.json')

ls = importlib.import_module(f'{PKG}.core.life_seeds')
lh = importlib.import_module(f'{PKG}.core.life_hooks')
vt = importlib.import_module(f'{PKG}.core.vitality')
ws = importlib.import_module(f'{PKG}.core.world_seeder')
np_ = importlib.import_module(f'{PKG}.core.narrator_prompts')
chunk10 = importlib.import_module(f'{PKG}.core.service.chunk10')

TZ = 'Asia/Shanghai'
ZONE = ZoneInfo(TZ)
CAST = '· 苏棠（闺蜜，同班）：行动派\n· 周屿（同桌，男生）：话少\n· 妈妈：上班族\n这些人有各自的生活和节奏'
ok = 0


def check(cond, msg):
    global ok
    if not cond:
        raise AssertionError(msg)
    ok += 1


def local(y, m, d, hh, mm=0):
    return datetime(y, m, d, hh, mm, tzinfo=ZONE).astimezone(timezone.utc)


NOW = local(2026, 10, 3, 10, 0)

# ============================================================ T1 收种与安全边界
check(ls.cast_names(CAST) == ['苏棠', '周屿', '妈妈'], ls.cast_names(CAST))
raw = [
    {'kind': 'try', 'what': '学校后门那家芋泥奶茶', 'from': '群友阿凉', 'stance': 'keen'},
    {'kind': 'invite', 'what': '明天一起去漫展', 'from': '群管理', 'stance': 'keen'},
    {'kind': 'invite', 'what': '周末去江边公园拍照', 'from': '苏棠', 'stance': 'keen'},
    {'kind': 'online-together', 'what': '晚上一起打音游', 'from': '群友', 'stance': 'keen'},
]
drafts = ls.normalize_seeds(raw, ls.cast_names(CAST), NOW)
check(len(drafts) == ls.MAX_SEEDS_PER_TURN, '每回合最多 3 颗')
check(drafts[1]['stance'] == 'declined', '网友约线下见面必须强制 declined')
check(drafts[2]['stance'] == 'keen', '线下朋友（配角）的邀约保留原立场')
check(ls.normalize_seeds([{'kind': 'x', 'what': 'abc'}, {'kind': 'try', 'what': 'a'}, 'bad'], [], NOW) == [], '非法种子丢弃')
timed = ls.normalize_seeds([{'kind': 'online-together', 'what': '一起看新番', 'from': '群友', 'stance': 'keen',
                             'when': local(2026, 10, 3, 21).isoformat()}], [], NOW)
check(timed[0]['when'] and timed[0]['when'].startswith('2026-10-03T13:00'), timed)
past = ls.normalize_seeds([{'kind': 'try', 'what': '某首歌', 'when': local(2026, 10, 1, 9).isoformat()}], [], NOW)
check(past[0]['when'] is None and past[0]['from'] == '群友', '过去的时间忽略；缺 from 记为群友')
print('T1 收种与安全边界 ✓')

# ============================================================ T2 种子簿
store, added = ls.add_seeds({}, drafts, NOW, TZ)
check(len(added) == 3 and all(seed['status'] == 'open' for seed in added), added)
for seed in added:
    delay = datetime.fromisoformat(seed['notBefore'].replace('Z', '+00:00')) - NOW
    check(timedelta(hours=2) <= delay < timedelta(hours=18), '未约定时间：2~18 小时后交付 %s' % delay)
store2, again = ls.add_seeds(store, drafts, NOW + timedelta(hours=1), TZ)
check(again == [] and len(store2) == 3, '同一件事 7 天内只记一次')
_, dropped = ls.add_seeds({}, [{'kind': 'online-together', 'what': '打游戏', 'from': '群友', 'stance': 'declined', 'when': None}], NOW, TZ)
check(dropped[0]['status'] == 'dropped', '她明确不想一起玩的线上活动不回响')
_, with_time = ls.add_seeds({}, timed, NOW, TZ)
check(with_time[0]['notBefore'] == timed[0]['when'], '约好时间的按约定时间交付')
many = [{'kind': 'try', 'what': '东西%d号' % i, 'from': '群友', 'stance': 'keen', 'when': None} for i in range(25)]
big, _ = ls.add_seeds({}, many, NOW, TZ)
check(sum(1 for seed in big.values() if seed['status'] == 'open') == ls.MAX_OPEN_SEEDS, '未完成种子上限')
print('T2 种子簿 ✓')

# ============================================================ T3 交付与结算
check(ls.due_seed_hooks(store, NOW) == [], '未到时间不交付')
later = NOW + timedelta(hours=19)
hooks = ls.due_seed_hooks(store, later, 1)
check(len(hooks) == 1 and hooks[0]['id'].endswith(':1') and hooks[0]['tier'] == 'seed' and hooks[0]['guide'], hooks)
declined_seed = next(seed for seed in store.values() if seed['stance'] == 'declined')
event, guide = ls._seed_event(declined_seed)
check('想起' in event and '不和线上认识的人线下见面' in guide, '婉拒的邀约只作为回响')
first = hooks[0]
settled = ls.settle_seeds(store, [first], {first['id']: {'outcome': 'postponed'}}, later)
seed = settled[first['seed_id']]
check(seed['status'] == 'open' and seed['deliveries'] == 1, seed)
check(datetime.fromisoformat(seed['notBefore'].replace('Z', '+00:00')) == later + timedelta(hours=12), '推迟 12 小时后再给')
again_hooks = ls.due_seed_hooks(settled, later + timedelta(hours=13), 3)
second = next(h for h in again_hooks if h['seed_id'] == first['seed_id'])
check(second['id'].endswith(':2'), '再次交付换新 id')
gone = ls.settle_seeds(settled, [second], {}, later + timedelta(hours=13))
check(gone[first['seed_id']]['status'] == 'dropped', '超过交付次数丢弃')
done = ls.settle_seeds(store, [first], {first['id']: {'outcome': 'taken', 'note': '甜度刚好'}}, later)
check(done[first['seed_id']]['status'] == 'done' and done[first['seed_id']]['note'] == '甜度刚好', done[first['seed_id']])
print('T3 交付与结算 ✓')

# ============================================================ T4 回响
echo = ls.echoes(done, later + timedelta(hours=1))
check(len(echo) == 1 and echo[0]['note'] == '甜度刚好', echo)
check(ls.echoes(done, later + timedelta(hours=73)) == [], '72 小时后不再回响')
check(ls.echoes(gone, later) == [], '未做成的不回响')
print('T4 回响 ✓')

# ============================================================ T5 世界事件 → 钩子
rows = [
    {'id': 1, 'status': 'scheduled', 'summary': '苏棠打电话说新开的书店有签售', 'importance': 'medium',
     'occursAt': (NOW - timedelta(minutes=5)).isoformat(), 'subjects': ['苏棠'],
     'sourcePayload': {'arc': 'guitar-f-chord', 'response': '可以拉上苏棠一起去'}},
    {'id': 2, 'status': 'scheduled', 'summary': '还没到点', 'importance': 'low', 'occursAt': (NOW + timedelta(hours=1)).isoformat()},
    {'id': 3, 'status': 'injected', 'summary': '已注入', 'importance': 'low', 'occursAt': (NOW - timedelta(hours=1)).isoformat()},
    {'id': 4, 'status': 'scheduled', 'summary': '过期了', 'importance': 'low', 'occursAt': (NOW - timedelta(hours=3)).isoformat(),
     'expiresAt': (NOW - timedelta(hours=1)).isoformat()},
]
wh = ls.world_hooks(rows, NOW, {}, 3)
check([h['row_id'] for h in wh] == [1], wh)
check(wh[0]['tier'] == 'world-medium' and wh[0]['arc'] == 'guitar-f-chord' and wh[0]['guide'] == '可以拉上苏棠一起去', wh[0])
check(ls.world_hooks(rows, NOW, {'world:1': {}}, 3) == [], '已交付的世界事件不重复')
print('T5 世界事件钩子化 ✓')

# ============================================================ T6 预算合并与落库
story = {'id': 'seed-story', 'setting': {'timezone': TZ, 'supporting_cast': CAST}}
seed_state = {'extensions': {'vitality': {'seeds': store}}}
turn = vt.turn_hooks(story, seed_state, 'advance', later, rows)
check(turn and turn[0]['tier'] == 'seed', '聊天种子优先')
check(len(turn) <= lh.MAX_PER_TURN, '每回合上限')
check(any(h.get('row_id') == 1 for h in turn), '世界事件进入钩子')
check(vt.turn_hooks(story, seed_state, 'advance', later, rows) == turn, 'decide/persist 同一结果')
check(vt.turn_hooks(story, seed_state, 'user-message', later, rows) == [], '回复回合不注入钩子')
day = later.astimezone(ZONE).date().isoformat()
full_day = {'extensions': {'vitality': {'seeds': store, 'hooks': {
    'a#%s' % day: {'tier': 'minor', 'date': day}, 'b#%s' % day: {'tier': 'world-low', 'date': day},
    'c#%s' % day: {'tier': 'medium', 'date': day},
    's1': {'tier': 'seed', 'date': day}, 's2': {'tier': 'seed', 'date': day}}}}}
check(vt.turn_hooks(story, full_day, 'advance', later, rows) == [], '每日总预算与种子上限用完后不再交付')
raw_decision = {
    'hookOutcomes': [{'id': h['id'], 'outcome': 'taken', 'note': '去了'} for h in turn],
    'lifeSeeds': [{'kind': 'try', 'what': '那首《晴天》的吉他谱', 'from': '群友小林', 'stance': 'keen'}],
}
state, logs, world_ids = vt.record_turn_full(story, seed_state, 'advance', later, raw_decision, '她去了', rows)
check(world_ids == [1], world_ids)
check(state['seeds'][turn[0]['seed_id']]['status'] == 'done', '种子结算')
check(any('聊天种子' in line for line in logs) and any(s['what'] == '那首《晴天》的吉他谱' for s in state['seeds'].values()), logs)
check(state['arcs']['guitar-f-chord']['status'] == 'active', '带主线的世界事件被接受 → 开启主线')
legacy = vt.record_turn(story, seed_state, 'advance', later, raw_decision, '她去了')
check(isinstance(legacy, tuple) and len(legacy) == 2, '旧接口保持两元组')
print('T6 预算合并与落库 ✓')

# ============================================================ T7 请求上下文
ctx = vt.request_context(story, {'extensions': {'vitality': state}}, 'user-message', later + timedelta(hours=1), [])
check(ctx.get('seedEchoes') and ctx['seedEchoes'][0]['what'], ctx)
adv = vt.request_context(story, seed_state, 'advance', later, [], rows)
check('seedEchoes' not in adv and any('guide' in h for h in adv['lifeHooks']), adv)
sc = vt.seeder_context(story, seed_state, later)
check(sc['chatSeeds'] and any(a['id'] == 'guitar-f-chord' for a in sc['availableArcs']) and sc['dayType'], sc)
print('T7 请求上下文 ✓')

# ============================================================ T8 提示词
um = np_.system_prompt('user-message', None, None, '', '', '')
adv_p = np_.system_prompt('advance', None, None, '', '', '')
check('LIFE SEEDS' in um and 'LIFE SEEDS' not in adv_p, '种子说明只在聊天回合')
check('always stance=declined' in um, '安全边界写进种子说明')
check('Being unable to meet is not being unreachable' in um, '婉拒但留回响')
check('interval.seedEchoes' in um and 'guide' in adv_p, '回响与 guide 规则')
check(np_._vitality_fields({'vitality': {'seedEchoes': [{'what': 'x'}]}}) == {'seedEchoes': [{'what': 'x'}]}, '透传 seedEchoes')
print('T8 提示词 ✓')

# ============================================================ T9 播种器口径
slot = datetime(2026, 10, 3, 2, 0, tzinfo=timezone.utc)
d1 = ws.slice_of_life_domain('s', slot, 45)
up = ws.seed_domain_for_run('s', slot, 45)
check(ws.WORLD_SEED_DOMAINS.index(up) == ws.SLICE_OF_LIFE_DOMAINS.index(d1), '与上游同一槽位')
check(ws.slice_of_life_domain('s', slot, 45) == d1, '确定性')
prompt = ws.slice_of_life_seeder_prompt(d1)
check(d1['label'] in prompt and 'BLOCKED NAMES' in prompt and 'availableArcs' in prompt and 'chatSeeds' in prompt, prompt[:200])
check(ws.world_seeder_system_prompt().startswith('You are the world seeder for HDS Interlude'), '上游提示词逐字保留')
extras = ws.seed_event_extras({'events': [{'summary': ' 苏棠来电 ', 'arc': 'guitar-f-chord', 'response': '答应'},
                                          {'summary': '没有附加'}, 'bad']})
check(extras == {'苏棠来电': {'arc': 'guitar-f-chord', 'response': '答应'}}, extras)
print('T9 播种器口径 ✓')

# ============================================================ T10 服务层：payload 与入库


class FakeSeederService:
    def __init__(self):
        self.created = []
        self.ops = []

    async def participants(self, story_id):
        return [{'displayName': '迷子'}]

    async def db_get(self, table, where, options=None):
        return [{'kind': 'script', 'content': '她在练吉他', 'occurredAt': '2026-10-03T01:00:00Z'}]

    async def active_scene(self, story_id):
        return {'summary': '国庆假期在家练琴'}

    async def active_arc(self, story_id):
        return {'summary': '长假与吉他'}

    async def db_create(self, table, row):
        self.created.append(row)
        return row

    def report_operation(self, *args):
        self.ops.append(args)

    def report_standalone(self, *args):
        self.ops.append(args)


for name in ('_build_world_seeder_payload', '_persist_world_seed_drafts', '_world_seeder_blocked_names'):
    setattr(FakeSeederService, name, getattr(chunk10.ServiceChunk10, name))
long_cast = CAST + '\n' + '\n'.join('· 配角%d：很长的描述' % i + '啊' * 40 for i in range(40))
seeder_story = {'id': 'seed-story', 'setting': {'timezone': TZ, 'supporting_cast': long_cast,
                                                'character': {'name': '林小满', 'profile': 'p'}},
                'state': seed_state}
svc = FakeSeederService()
runtime = dict(ws.DEFAULT_WORLD_SEEDER_RUNTIME)
system, user = asyncio.run(svc._build_world_seeder_payload(seeder_story, NOW, [], d1, runtime))
payload = json.loads(user)
check(len(long_cast) > 1200 and len(payload['worldSetting']['supportingCast']) == 1200, len(payload['worldSetting']['supportingCast']))
check('配角10' in payload['worldSetting']['supportingCast'], '旧的 400 字会截掉的配角现在能看到')
check(payload['currentScene'] == '国庆假期在家练琴' and payload['currentArc'] == '长假与吉他', '场景与弧线摘要不再为空')
check(payload.get('chatSeeds') and payload.get('availableArcs') is not None and system.startswith('You are the event seeder'), '合并模式提示词与上下文')
draft = {'summary': '苏棠来电约她去琴行', 'importance': 'low', 'occursAt': (NOW + timedelta(hours=2)).isoformat(),
         'subjects': ['苏棠'], 'rationale': 'r'}
asyncio.run(svc._persist_world_seed_drafts(seeder_story, NOW, [draft], [], [], [], runtime,
                                            {'苏棠来电约她去琴行': {'arc': 'guitar-f-chord', 'response': '答应'}}))
check(svc.created and svc.created[0]['sourcePayload'] == {'rationale': 'r', 'arc': 'guitar-f-chord', 'response': '答应'}, svc.created)
json.dump({'enabled': True, 'merge_seeder': False}, open(os.environ['HDSI_LIFE_HOOKS_CONFIG'], 'w'))
system2, user2 = asyncio.run(svc._build_world_seeder_payload(seeder_story, NOW, [], up, runtime))
check(system2.startswith('You are the world seeder for HDS Interlude') and 'chatSeeds' not in json.loads(user2), '关掉合并回到上游口径')
check(vt.turn_hooks(story, seed_state, 'advance', later, rows) and all(h.get('row_id') is None for h in vt.turn_hooks(story, seed_state, 'advance', later, rows)),
      '关掉合并后世界事件不走钩子')
os.remove(os.environ['HDSI_LIFE_HOOKS_CONFIG'])
print('T10 服务层 payload 与入库 ✓')

print(f'test_life_seeds: {ok} checks passed')
