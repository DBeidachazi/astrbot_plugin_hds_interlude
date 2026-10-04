"""颜文字频率控制（Expression Budget）测试。

用法：PYTHONPATH=<插件父目录> python test_expression.py <插件包名>
"""

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

ex = importlib.import_module(f'{PKG}.core.expression')
vt = importlib.import_module(f'{PKG}.core.vitality')
np_ = importlib.import_module(f'{PKG}.core.narrator_prompts')
cc_mod = importlib.import_module(f'{PKG}.core.script.context_compiler')
ok = 0
SEP = '<sep/>'


def check(cond, msg):
    global ok
    if not cond:
        raise AssertionError(msg)
    ok += 1


def faces(text):
    return [face for _s, _e, face in ex.find_kaomoji(text)]


# ============================================================ T1 识别
positives = {
    '不谈呀！只有做不完的卷子 (＞﹏＜)': ['(＞﹏＜)'],
    '等考上再说啦 (｡･ω･｡)': ['(｡･ω･｡)'],
    '呜呜qwq': ['qwq'],
    'QAQ 好难': ['QAQ'],
    '_(:з」∠)_ 不想动': ['_(:з」∠)_'],
    '冲呀(๑•̀ㅂ•́)و✧': ['(๑•̀ㅂ•́)و✧'],
    '好耶（≧∇≦）': ['（≧∇≦）'],
    '无语 (￣▽￣)': ['(￣▽￣)'],
    '哭了 (T_T)': ['(T_T)'],
    '两个 (｡･ω･｡) 和 (＞﹏＜)': ['(｡･ω･｡)', '(＞﹏＜)'],
}
for text, expected in positives.items():
    check(faces(text) == expected, '%s → %s' % (text, faces(text)))
negatives = ['（周六补课）记得带卷子', '文件是(PDF)格式', '进度 (3/5)', '涨了 (10%)', '他说（大概）会来',
             '函数 f(x) 的值', '看这个 (https://a.b)', '今天好开心', 'twqq 不是颜文字', '数学(必修一)']
for text in negatives:
    check(faces(text) == [], '误判为颜文字：%s → %s' % (text, faces(text)))
check(ex.has_kaomoji('哈哈 qwq') and not ex.has_kaomoji('哈哈'), 'has_kaomoji')
print('T1 识别 ✓')

# ============================================================ T2 只删句尾 / 独立颜文字
cases = {
    '不谈呀！只有做不完的卷子 (＞﹏＜)': '不谈呀！只有做不完的卷子',
    '奇变偶不变 符号看象限！(｡･ω･｡)': '奇变偶不变 符号看象限！',
    '好累 (T_T)！！': '好累！！',                    # 保留后面的标点
    '我(｡･ω･｡)今天好困': '我(｡･ω･｡)今天好困',        # 句中的保留
    '_(:з」∠)_ 好累': '_(:з」∠)_ 好累',              # 句首的保留
    'qwq': '',                                       # 独立成段：删空
    '（周六补课）记得带卷子': '（周六补课）记得带卷子',  # 普通括号不动
    '两个 (｡･ω･｡)(＞﹏＜)': '两个',                    # 句尾连续多个
}
for text, expected in cases.items():
    check(ex.strip_trailing_kaomoji(text) == expected, '%s → %r' % (text, ex.strip_trailing_kaomoji(text)))
check(ex.regulate_message('哈哈 (｡･ω･｡)<sep/>qwq', SEP) == '哈哈', '独立气泡被删、空气泡不留')
check(ex.regulate_message('qwq', SEP) == 'qwq', '整条消息只有颜文字时保留原文（不发空消息）')
print('T2 只删句尾 ✓')

# ============================================================ T3 滑动窗口与额度状态
plain = ['今天写卷子', '好困', '吃饭去', '物理好难', '晚安']
cfg = json.loads(json.dumps(ex.DEFAULT_CONFIG))
b = ex.kaomoji_budget(plain, None, cfg)
check(b['state'] == 'free' and b['count'] == 0 and b['cap'] == 4, b)
b = ex.kaomoji_budget(plain + ['哈哈 (｡･ω･｡)'], None, cfg)
check(b['state'] == 'rest' and b['avoid'] == ['(｡･ω･｡)'], '上一条刚用过 → rest，并提示避开: %s' % b)
b = ex.kaomoji_budget(['a (｡･ω･｡)', 'b', 'c (＞﹏＜)', 'd', 'e', 'f'], None, cfg)
check(b['state'] == 'free', '2/10 < 0.25 → free: %s' % b)
b = ex.kaomoji_budget(['a (｡･ω･｡)', 'b', 'c (＞﹏＜)', 'd', 'e qwq', 'f'], None, cfg)
check(b['state'] == 'sparing' and b['count'] == 3, '3/10 ≥ 0.25 → sparing: %s' % b)
full = ['a (｡･ω･｡)', 'b', 'c (＞﹏＜)', 'd', 'e qwq', 'f', 'g (T_T)', 'h']
b = ex.kaomoji_budget(full, None, cfg)
check(b['state'] == 'rest' and b['count'] == 4, '达到窗口上限 → rest: %s' % b)
old = ['x (｡･ω･｡)'] * 6 + ['今天写卷子'] * 10
check(ex.kaomoji_budget(old, None, cfg)['count'] == 0, '窗口外的不计')
no_repeat = dict(cfg, kaomoji=dict(cfg['kaomoji'], avoid_repeat=False))
check(ex.kaomoji_budget(plain + ['哈哈 (｡･ω･｡)'], None, no_repeat)['state'] == 'free', '关掉 avoid_repeat 后不因上一条 rest')
print('T3 窗口与额度 ✓')

# ============================================================ T4 心情自适应
lively = {'alter_system': {'alter_value': -3}}
heavy = {'alter_system': {'alter_value': 3}}
b_l, b_h, b_n = (ex.kaomoji_budget(plain, s, cfg) for s in (lively, heavy, None))
check(b_l['target'] == 0.4 and b_l['cap'] == 5, b_l)
check(b_h['target'] == 0.2 and b_h['cap'] == 3, b_h)
check(b_n['target'] == 0.3 and b_n['cap'] == 4, b_n)
three = ['a (｡･ω･｡)', 'b', 'c (＞﹏＜)', 'd', 'e qwq', 'f']
check(ex.kaomoji_budget(three, heavy, cfg)['state'] == 'rest', '氛围沉重：上限 3 → rest')
still = dict(cfg, kaomoji=dict(cfg['kaomoji'], mood_adaptive=False))
check(ex.kaomoji_budget(plain, lively, still)['target'] == 0.3, '关掉 mood_adaptive 不调整')
print('T4 心情自适应 ✓')

# ============================================================ T5 硬兜底 + 剧本同步
script = '她看着群里笑出声，飞快敲了一句发出去：不谈呀！只有做不完的卷子 (＞﹏＜)\n\n然后把手机扣在桌上。'
reply = {'mode': 'immediate', 'content': '不谈呀！只有做不完的卷子 (＞﹏＜)'}
decision = {'script': script, 'groupReply': reply, 'group_reply': dict(reply),
            'authored_actions': [{'id': 'reply', 'start': script.index('不谈'),
                                  'end': script.index('不谈') + len(reply['content']), 'content': reply['content']}]}
actions_list = decision['authored_actions']
logs = ex.guard_decision(decision, plain + ['哈哈 (｡･ω･｡)'], None, SEP, cfg)
check(decision['groupReply']['content'] == '不谈呀！只有做不完的卷子' == decision['group_reply']['content'], decision)
check('(＞﹏＜)' not in decision['script'] and '不谈呀！只有做不完的卷子' in decision['script'], '剧本同步')
check(decision['authored_actions'] is actions_list, 'authored_actions 保持同一个列表对象')
act = decision['authored_actions'][0]
check(act['content'] == '不谈呀！只有做不完的卷子' and decision['script'][act['start']:act['end']] == act['content'], act)
check(logs and '原因=rest' in logs[0], logs)
# 额度允许时不动
ok_decision = {'script': 's 好耶 (≧∇≦)', 'groupReply': {'mode': 'immediate', 'content': '好耶 (≧∇≦)'}}
check(ex.guard_decision(ok_decision, plain, None, SEP, cfg) == [] and ok_decision['groupReply']['content'] == '好耶 (≧∇≦)', '额度内不动')
# 与上一条同一个颜文字 → repeat（即使状态允许）
rep_cfg = dict(cfg, kaomoji=dict(cfg['kaomoji'], max_in_window=9, sparing_at=0.9))
rep = {'script': '好耶 (｡･ω･｡)', 'interaction': {'seen': True, 'reply': {'mode': 'immediate', 'content': '好耶 (｡･ω･｡)'}}}
logs = ex.guard_decision(rep, ['a', 'b (｡･ω･｡)'], None, SEP, dict(rep_cfg, kaomoji=dict(rep_cfg['kaomoji'], avoid_repeat=True)))
check(rep['interaction']['reply']['content'] == '好耶' and logs, rep)
# 句中颜文字不动；多条消息逐条计入窗口
mid = {'script': 'x', 'groupReply': {'mode': 'immediate', 'content': '我(｡･ω･｡)今天好困'}}
ex.guard_decision(mid, plain + ['哈哈 (｡･ω･｡)'], None, SEP, cfg)
check(mid['groupReply']['content'] == '我(｡･ω･｡)今天好困', '句中颜文字不删')
multi = {'script': 'A 好耶 (≧∇≦) B 冲呀 (๑•̀ㅂ•́)و✧',
         'groupReply': {'mode': 'immediate', 'content': '好耶 (≧∇≦)'},
         'crossConversationActions': [{'participantId': 'p', 'mode': 'immediate', 'content': '冲呀 (๑•̀ㅂ•́)و✧'}]}
ex.guard_decision(multi, plain, None, SEP, cfg)
check(multi['groupReply']['content'] == '好耶 (≧∇≦)' and multi['crossConversationActions'][0]['content'] == '冲呀',
      '第一条用掉额度后，同回合第二条按 rest 处理: %s' % multi)
# 气泡全是颜文字：连同分隔符从剧本里去掉
bub = {'script': '她发：好耶<sep/>qwq', 'groupReply': {'mode': 'immediate', 'content': '好耶<sep/>qwq'}}
ex.guard_decision(bub, plain + ['x (T_T)'], None, SEP, cfg)
check(bub['groupReply']['content'] == '好耶' and bub['script'] == '她发：好耶', bub)
# 关闭开关
off = json.loads(json.dumps(cfg)); off['kaomoji']['hard_guard'] = False
keep = {'script': 'x (＞﹏＜)', 'groupReply': {'mode': 'immediate', 'content': 'x (＞﹏＜)'}}
check(ex.guard_decision(keep, plain + ['y (T_T)'], None, SEP, off) == [] and keep['groupReply']['content'] == 'x (＞﹏＜)', 'hard_guard 关闭')
check(ex.guard_decision(None, [], None, SEP, cfg) == [], '坏输入')
print('T5 硬兜底与剧本同步 ✓')

# ============================================================ T6 统计她的消息 / 提示词 / 配置
entries = [
    {'kind': 'character-group-message', 'occurredAt': '2026-10-04T02:00:00Z', 'content': 'a (｡･ω･｡)'},
    {'kind': 'group-message', 'occurredAt': '2026-10-04T02:01:00Z', 'content': '别人的 (＞﹏＜)'},
    {'kind': 'script', 'occurredAt': '2026-10-04T02:02:00Z', 'content': '剧本 (＞﹏＜)'},
    {'kind': 'character-message', 'occurredAt': '2026-10-04T02:03:00Z', 'content': 'b'},
]
check(ex.her_messages(entries) == ['a (｡･ω･｡)', 'b'], '只统计她发出的消息')
budget = ex.prompt_budget(ex.her_messages(entries), None, cfg)
check(budget['kaomoji'] == 'free' and budget['avoid'] == ['(｡･ω･｡)'] and 'exclamation' not in budget, budget)
exc = json.loads(json.dumps(cfg)); exc['exclamation']['enabled'] = True
check(ex.prompt_budget(['好！'] * 7, None, exc).get('exclamation') == 'sparing', '感叹号软反馈')
story = {'id': 's', 'setting': {'timezone': 'Asia/Shanghai'}}
from datetime import datetime, timezone  # noqa: E402
ctx = vt.request_context(story, {}, 'user-message', datetime(2026, 10, 4, 3, 0, tzinfo=timezone.utc), entries)
check(ctx.get('expressionBudget', {}).get('kaomoji') in ('free', 'sparing', 'rest'), ctx)
fields = np_._vitality_fields({'vitality': {'expressionBudget': budget}})
check(fields.get('expressionBudget') == budget, '提示词字段透传')
compiled = cc_mod.compile_narrative_context({'interval': {'nowLocal': 'x', 'expressionBudget': budget}, 'phase': 'user-message'}, None, None)
check('expressionBudget' in compiled['authoringWindow']['interval'], '编译保留')
sysp = np_.system_prompt('user-message', None, None, '', '', '')
check('interval.expressionBudget' in sysp and 'emotional punctuation' in sysp, '提示词规则')
# 热读取 + 关闭
json.dump({'kaomoji': {'target_rate': 0.5}}, open(os.environ['HDSI_EXPRESSION_CONFIG'], 'w'))
check(ex.load_config()['kaomoji']['target_rate'] == 0.5 and ex.load_config()['kaomoji']['window'] == 10, '热读取 + 缺省键补默认')
json.dump({'enabled': False}, open(os.environ['HDSI_EXPRESSION_CONFIG'], 'w'))
os.utime(os.environ['HDSI_EXPRESSION_CONFIG'], (1, 2))  # 确保 mtime 变化
check(ex.prompt_budget(['a'], None) is None, '整体关闭后不注入')
off_dec = {'script': 'x (＞﹏＜)', 'groupReply': {'mode': 'immediate', 'content': 'x (＞﹏＜)'}}
check(ex.guard_decision(off_dec, ['y (T_T)'], None, SEP) == [], '整体关闭后不兜底')
os.remove(os.environ['HDSI_EXPRESSION_CONFIG'])
print('T6 统计 / 提示词 / 配置 ✓')

print(f'test_expression: {ok} checks passed')
