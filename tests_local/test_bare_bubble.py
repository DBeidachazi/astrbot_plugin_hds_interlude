"""漏写 `<say>` 的裸气泡块兜底测试（纯本地，无需 AstrBot 依赖之外的东西）。

用法：PYTHONPATH=<插件父目录> python test_bare_bubble.py <插件包名>
"""

import importlib
import sys

PKG = sys.argv[1] if len(sys.argv) > 1 else 'hds_interlude'

aa = importlib.import_module(f'{PKG}.core.script.authored_actions')
helpers = importlib.import_module(f'{PKG}.core.service.helpers')
np_ = importlib.import_module(f'{PKG}.core.narrator_prompts')
sole_bubble_block = aa.sole_bubble_block
resolve = aa.resolve_authored_actions
repair = helpers.repair_missing_visible_reply
needs_recovery = helpers.requires_visible_reply_recovery

SEP = '<sep/>'
GROUP = {'group_id': 'g'}
ok = 0


def check(cond, msg):
    global ok
    if not cond:
        raise AssertionError(msg)
    ok += 1


# 生产实例（2026-10-01）：模型叙述了「敲出两句发进群里：……」却没包 <say>。
CASE = """枕头陷下去的弧度软绵绵的，林小满刚揉了两下煤球温热的小肚皮，屏幕上方就接连跳出两条新消息——群友极为真诚地问了句“任天堂是啥”，紧接着管理转头就召唤机器人“……”。

“诶？居然真的有人不知道任天堂呀……”

小满愣了半秒，随即下巴磕在软枕上噗嗤乐出了声。

初秋上午的暖阳把被面晒得蓬松又暖和，她两手举着手机，指尖在键盘上轻快敲出两句发进群里：居然不知道任天堂嘛！就是做马里奥、宝可梦和塞尔达的那个大厂啦<sep/>大家玩的Switch掌机也是他们家的(｡･ω･｡)

消息嗖地甩出去后，小满顺手把胳膊搭回被面上。"""
EXPECTED = '居然不知道任天堂嘛！就是做马里奥、宝可梦和塞尔达的那个大厂啦<sep/>大家玩的Switch掌机也是他们家的(｡･ω･｡)'

# --- sole_bubble_block：叙述引子 / 引号 / 歧义 ---
check(sole_bubble_block(CASE, SEP) == EXPECTED, '同段叙述引子应被剥掉')
check(sole_bubble_block('提醒：明天考试<sep/>别忘了带笔', SEP) == '提醒：明天考试<sep/>别忘了带笔',
      '话里自带的冒号不能切')
check(sole_bubble_block('跟你说：明天考试<sep/>别忘了', SEP) == '跟你说：明天考试<sep/>别忘了',
      '过短的冒号前缀视为话本身')
check(sole_bubble_block('她回了一句：“好呀<sep/>等我”', SEP) == '好呀<sep/>等我', '整块外层引号')
check(sole_bubble_block('她飞快打字：“好呀”<sep/>“等我”', SEP) == '好呀<sep/>等我', '逐条引号')
check(sole_bubble_block('甲<sep/>乙\n\n丙<sep/>丁', SEP) is None, '两段候选必须拒绝')
check(sole_bubble_block('她发了句：好呀', SEP) is None, '无分隔符的单条不猜')

# --- 群聊：声明了 actionId / 完全没写 groupReply ---
for group_reply in ({'mode': 'immediate', 'actionId': 'reply'}, None):
    raw = {'script': CASE}
    if group_reply:
        raw['groupReply'] = group_reply
    decision = resolve(raw, False, SEP)
    repaired, kind = repair(decision, 'user-message', GROUP, False, SEP)
    check(kind == 'group-bare-bubble', f'群聊 {group_reply} 应补齐，实际 {kind!r}')
    check(repaired['groupReply']['content'] == EXPECTED, '群聊补齐内容')
    check(not needs_recovery('user-message', GROUP, repaired), '补齐后不应再触发重写')
    # normalize_decision 会按 snake_case 再解析一次引用：补上的内容必须存活。
    again = resolve({**repaired, 'group_reply': repaired['group_reply']}, False, SEP)
    check(again['group_reply'].get('content') == EXPECTED, '二次解析后内容应保留')

# --- 群聊：模型把回复写进 interaction 且 mode=none / 引用失配（2026-10-01 10:04 实测）---
for interaction in ({'seen': True, 'reply': {'mode': 'none'}},
                    {'seen': True, 'reply': {'mode': 'none', 'actionId': 'reply'}},
                    {'reply': {'mode': 'none'}}):
    for group_reply in (None, {'mode': 'immediate', 'actionId': 'reply'}, {'mode': 'none'}):
        raw = {'script': CASE, 'interaction': interaction}
        if group_reply:
            raw['groupReply'] = group_reply
        decision = resolve(raw, False, SEP)
        repaired, kind = repair(decision, 'user-message', GROUP, False, SEP)
        check(kind == 'group-bare-bubble' and repaired['groupReply']['content'] == EXPECTED,
              f'群聊 interaction={interaction} groupReply={group_reply} 应补齐，实际 {kind!r}')
        check(helpers.visible_reply_mode(repaired, 'user-message', GROUP) == 'group:immediate',
              '补齐后日志应显示 group:immediate')
        check(helpers.normalize_group_visible_reply(
            repaired.get('groupReply'), repaired.get('interaction'), 500, SEP) == EXPECTED, '发送端能取到内容')
# interaction 已带可投递内容：照旧走 group-fallback，不改写。
decision = {'script': CASE, 'interaction': {'seen': True, 'reply': {'mode': 'immediate', 'content': '好呀'}}}
check(repair(decision, 'user-message', GROUP, False, SEP)[1] == '', 'interaction 有内容时不动')
# interaction=none 且剧本里没有裸气泡：真的不回，保持原样。
decision = resolve({'script': '她看了一眼没回。', 'interaction': {'seen': True, 'reply': {'mode': 'none'}}}, False, SEP)
check(repair(decision, 'user-message', GROUP, False, SEP)[1] == '', '真的不回保持沉默')

# --- 私聊：interaction 缺失 / 声明了 actionId 却没写 say ---
repaired, kind = repair(resolve({'script': CASE}, False, SEP), 'user-message', None, True, SEP)
check(kind == 'private-bare-bubble' and repaired['interaction']['reply']['content'] == EXPECTED,
      '私聊缺 interaction 应补齐')
decision = resolve({'script': CASE, 'interaction': {
    'seen': True, 'reply': {'mode': 'immediate', 'actionId': 'reply'}}}, False, SEP)
check(decision['interaction']['reply'].get('content') == EXPECTED, '私聊 actionId 无 say 应取回且不带叙述')

# --- 原有路径不变 ---
decision = resolve({'script': '她笑了。<say id="reply">好<sep/>呀</say>',
                    'groupReply': {'mode': 'immediate', 'actionId': 'reply'}}, False, SEP)
repaired, kind = repair(decision, 'user-message', GROUP, False, SEP)
check(kind == 'group-reply-bound' and repaired['groupReply']['content'] == '好<sep/>呀', '正常 say 路径')
repaired, kind = repair(resolve({'script': '她看了一眼没回。'}, False, SEP), 'user-message', GROUP, False, SEP)
check(kind == 'group-silent', '没有裸气泡块仍判沉默')
repaired, kind = repair(resolve({'script': '她写了<say id="a">半截'}, False, SEP), 'user-message', GROUP, False, SEP)
check(kind == '', '残留 say 标记不补救')

# --- 提示词硬规则只给非流式路径 ---
# 合并 KelaLeaf（上游 rc16）后非流式改为 content-only 协议，不再教 <say>；HARD RULE 随之移除。
group_rule = np_.script_first_transport_instruction('user-message', True)
check('HARD RULE' not in group_rule and '<say' not in group_rule, '群聊提示词改为 content-only')
check('return groupReply as {"mode":"immediate","content"' in group_rule, '群聊 content-only 传输说明')

print(f'test_bare_bubble: {ok} checks passed')
