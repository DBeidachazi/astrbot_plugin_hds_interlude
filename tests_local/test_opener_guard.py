"""开头问号口癖硬兜底测试。

用法：PYTHONPATH=<插件父目录> python test_opener_guard.py <插件包名>
"""

import importlib
import json
import os
import sys
import tempfile

PKG = sys.argv[1] if len(sys.argv) > 1 else 'hds_interlude'
tmp = tempfile.mkdtemp()
os.environ['HDSI_EXPRESSION_CONFIG'] = os.path.join(tmp, 'expression.json')
ex = importlib.import_module(f'{PKG}.core.expression')
np_ = importlib.import_module(f'{PKG}.core.narrator_prompts')
SEP = '<sep/>'
ok = 0


def check(cond, msg):
    global ok
    if not cond:
        raise AssertionError(msg)
    ok += 1


def cfg():
    return json.loads(json.dumps(ex.DEFAULT_CONFIG))


def decision(text, script_prefix='她敲了一句发出去：'):
    return {'script': script_prefix + text.replace(SEP, '') + '\n\n然后放下手机。',
            'groupReply': {'mode': 'immediate', 'content': text}, 'group_reply': {'mode': 'immediate', 'content': text}}


# 2026-10-04 11:17–11:56 的真实样本：20 条里 18 条以问号开头
REAL = ['？？？好家伙你还真跑去查词典了！', '？？？我哪知道啦！', '？？？话题怎么突然变成铁锅炖了！', '？？？这也能分析的吗！',
        '？？？少道德绑架我啦！', '？？还打错字！', '？？你怎么又开始考英语完形填空了啦！', '？？？你怎么还玩上套娃翻译了啦！',
        '？？？我日语全靠看番！', '？？？我怎么就没有灵魂啦！']

# ============================================================ T1 剪开头
cases = {
    '？？？好家伙你还真跑去查词典了！': '好家伙你还真跑去查词典了！',
    '??我哪知道啦！': '我哪知道啦！',
    '？！你说什么': '你说什么',
    '？ 嗯': '嗯',
    '  ？？还打错字！': '还打错字！',
}
for raw, expected in cases.items():
    check(ex.strip_leading_questions(raw) == expected, '%r → %r' % (raw, ex.strip_leading_questions(raw)))
check(ex.starts_with_question('？？？x') and ex.starts_with_question(' ?x') and not ex.starts_with_question('好的？'), 'starts_with_question')
check(ex.strip_leading_questions('好的？') == '好的？', '句尾问号不动')
print('T1 剪开头 ✓')

# ============================================================ T2 触发条件
clean = ['好呀', '在写卷子', '晚安', '嗯嗯', '物理好难']
d = decision('？？？反转了是吧！')
check(ex.guard_decision(d, clean, None, SEP, cfg()) == [] and d['groupReply']['content'] == '？？？反转了是吧！',
      '历史干净、上一条不是问号开头：偶尔一次放行')
d = decision('？？？反转了是吧！')
logs = ex.guard_decision(d, clean + ['？你在干嘛'], None, SEP, cfg())
check(d['groupReply']['content'] == '反转了是吧！' and any('consecutive' in line for line in logs), '上一条也是问号开头 → 剪')
d = decision('？？？反转了是吧！')
hist = ['？a', '好', '？b', '嗯', '？c', '行', '？d', '好的']   # 窗口内 4 条问号开头，上一条不是
logs = ex.guard_decision(d, hist, None, SEP, cfg())
check(d['groupReply']['content'] == '反转了是吧！' and any('over-cap' in line for line in logs), '窗口内达到上限 → 剪: %s' % logs)
d = decision('？？？测了一整晚你终于下结论了啦！')
ex.guard_decision(d, REAL, None, SEP, cfg())
check(d['groupReply']['content'] == '测了一整晚你终于下结论了啦！' == d['group_reply']['content'], '真实场景：两份拼写都剪')
print('T2 触发条件 ✓')

# ============================================================ T3 剧本同步 + authored_actions
text = '？？？好家伙你还真跑去查词典了！'
script = '她扑哧一笑，飞快回了一句：' + text + '\n\n然后把手机扣在桌上。'
start = script.index(text)
actions = [{'id': 'reply', 'start': start, 'end': start + len(text), 'content': text}]
d = {'script': script, 'groupReply': {'mode': 'immediate', 'content': text}, 'authored_actions': actions}
ex.guard_decision(d, REAL, None, SEP, cfg())
check('？？？' not in d['script'] and '飞快回了一句：好家伙你还真跑去查词典了！' in d['script'], d['script'])
check(d['authored_actions'] is actions, '保持同一个列表对象')
act = d['authored_actions'][0]
check(act['content'] == '好家伙你还真跑去查词典了！' and d['script'][act['start']:act['end']] == act['content'], act)
print('T3 剧本同步 ✓')

# ============================================================ T4 多气泡 / 只有问号 / 与颜文字叠加 / 同回合累计
d = decision('？？你怎么又来了' + SEP + '？？？我日语全靠看番！')
ex.guard_decision(d, REAL, None, SEP, cfg())
check(d['groupReply']['content'] == '你怎么又来了' + SEP + '我日语全靠看番！', '每个以问号开头的气泡都剪')
d = decision('？？？')
check(ex.guard_decision(d, REAL, None, SEP, cfg()) and d['groupReply']['content'] == '？？？', '整条只有问号：保留（不发空消息）')
d = decision('？？？' + SEP + '你在说什么啦')
ex.guard_decision(d, REAL, None, SEP, cfg())
check(d['groupReply']['content'] == '你在说什么啦', '只有问号的气泡被去掉，其余保留')
d = decision('？？？好耶 (｡･ω･｡)')
ex.guard_decision(d, REAL + ['哈哈 (｡･ω･｡)'], None, SEP, cfg())
check(d['groupReply']['content'] == '好耶', '颜文字与问号同时处理: %r' % d['groupReply']['content'])
multi = {'script': 'A ？？a B ？？b',
         'groupReply': {'mode': 'immediate', 'content': '？？a'},
         'crossConversationActions': [{'participantId': 'p', 'mode': 'immediate', 'content': '？？b'}]}
ex.guard_decision(multi, clean, None, SEP, cfg())
check(multi['groupReply']['content'] == '？？a' and multi['crossConversationActions'][0]['content'] == 'b',
      '同回合第一条放行后，第二条按「上一条是问号开头」剪: %s' % multi)
print('T4 多气泡与叠加 ✓')

# ============================================================ T5 配置与提示词
off = cfg(); off['opener']['hard_guard'] = False
d = decision('？？？反转了是吧！')
ex.guard_decision(d, REAL, None, SEP, off)
check(d['groupReply']['content'] == '？？？反转了是吧！', 'opener.hard_guard 关闭后只软提示')
both_off = cfg(); both_off['opener']['hard_guard'] = False; both_off['kaomoji']['hard_guard'] = False
check(ex.guard_decision(decision('？？x (｡･ω･｡)'), REAL, None, SEP, both_off) == [], '两个硬兜底都关')
kao_off = cfg(); kao_off['kaomoji']['hard_guard'] = False
d = decision('？？？好耶')
ex.guard_decision(d, REAL, None, SEP, kao_off)
check(d['groupReply']['content'] == '好耶', '只关颜文字兜底时问号兜底仍工作')
json.dump({'opener': {'max_in_window': 9}}, open(os.environ['HDSI_EXPRESSION_CONFIG'], 'w'))
check(ex.load_config()['opener']['hard_guard'] is True and ex.load_config()['opener']['max_in_window'] == 9, '热读取 + 默认键补齐')
os.remove(os.environ['HDSI_EXPRESSION_CONFIG'])
sysp = np_.system_prompt('user-message', None, None, '', '', '', group_turn=True)
check('Never open messages with ？' in sysp and 'host trims a leading ？' in sysp, '提示词常驻规则')
print('T5 配置与提示词 ✓')

print(f'test_opener_guard: {ok} checks passed')
