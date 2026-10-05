"""Alter 侧端分析触发（键名兼容）与积压桶夹紧测试。

用法：PYTHONPATH=<插件父目录> python test_alter_trigger.py <插件包名>
"""

import importlib
import sys
from datetime import datetime, timedelta, timezone

PKG = sys.argv[1] if len(sys.argv) > 1 else 'hds_interlude'
alter = importlib.import_module(f'{PKG}.core.alter')
chunk4 = importlib.import_module(f'{PKG}.core.service.chunk4')
NOW = datetime(2026, 10, 5, 11, 23, tzinfo=timezone.utc)
CONFIG = {'enabled': True, 'base_threshold': 10.0, 'density_factor': 0.3, 'same_direction_boost': 0.05,
          'opposite_decay': 0.15, 'min_weight': 0.2, 'max_intensity': 2.0}
ME = 'onebot:1690619901:269502169'
ok = 0


def check(cond, msg):
    global ok
    if not cond:
        raise AssertionError(msg)
    ok += 1


# 2026-10-05 生产状态（摘录）：三个桶，总 -192，从未尝试过分析
PROD = {
    'alter_value': -192, 'alter_weight': 0, 'last_trigger_direction': 0, 'emotional_offset': None,
    'history': [{'turn': 600 + i, 'phase': 'user-message', 'alter': -1, 'alter_value': -180 - i,
                 'timestamp': (NOW - timedelta(minutes=5 * (12 - i))).isoformat().replace('+00:00', 'Z')}
                for i in range(12)],
    'pending_scopes': [{'participant_id': '', 'alter_value': -172},
                       {'participant_id': ME, 'alter_value': -17},
                       {'participant_id': 'onebot:1690619901:458593826', 'alter_value': -3}],
    'last_analysis_attempt_at': None,
}

# ============================================================ T1 触发判定：两种拼写
result = alter.advance_alter_system(PROD, -1, 'user-message', NOW, CONFIG, '')
check('threshold_reached' in result and 'thresholdReached' not in result, '上游函数返回的是 snake_case: %s' % list(result))
check(result['threshold_reached'] is True, '主角自身桶 -173 远超阈值')
check(chunk4._alter_analysis_trigger(result) == '', '修复后：主角自身桶触发（来源 = 空串）')
mine = alter.advance_alter_system(PROD, -1, 'user-message', NOW, CONFIG, ME)
check(chunk4._alter_analysis_trigger(mine) == ME, '关系桶触发，来源是这个参与者')
small = alter.advance_alter_system(None, -1, 'user-message', NOW, CONFIG, '')
check(small['threshold_reached'] is False and chunk4._alter_analysis_trigger(small) is None, '没到阈值：不触发')
check(chunk4._alter_analysis_trigger({'thresholdReached': True, 'sourceParticipantId': 'p'}) == 'p', 'camelCase 也认')
check(chunk4._alter_analysis_trigger(None) is None and chunk4._alter_analysis_trigger({}) is None, '空值')
# 回归：旧写法（只读 camelCase）在真实返回值上永远是 False —— 这就是从未触发的原因
check(not {**result}.get('thresholdReached'), '旧写法读不到')
print('T1 触发判定 ✓')

# ============================================================ T2 积压桶夹紧
clamped, changes = alter.clamp_alter_backlog(PROD, 8.0)
check(changes == [('', -172, -8.0), (ME, -17, -8.0)], changes)
check([s['alter_value'] for s in clamped['pending_scopes']] == [-8.0, -8.0, -3], clamped['pending_scopes'])
check(clamped['alter_value'] == -19.0, '总位移重算: %s' % clamped['alter_value'])
check(clamped['history'] == PROD['history'] and PROD['pending_scopes'][0]['alter_value'] == -172, '历史保留、原状态不被改动')
again, changes2 = alter.clamp_alter_backlog(clamped, 8.0)
check(changes2 == [] and again is clamped, '幂等')
pos, ch = alter.clamp_alter_backlog({'pendingScopes': [{'participantId': 'x', 'alterValue': 30}], 'alterValue': 30}, 8)
check(ch == [('x', 30, 8.0)] and pos['alterValue'] == 8.0, '正方向、camelCase')
check(alter.clamp_alter_backlog(None) == (None, []) and alter.clamp_alter_backlog({'alter_value': 3}) == ({'alter_value': 3}, []), '空 / 没有桶')
# 夹紧后第一次分析的强度是温和的（而不是直接顶到上限 2.0）
threshold = alter.calculate_alter_threshold(alter.alter_history_for_scope(clamped['history'], ''), CONFIG, NOW)
done = alter.complete_alter_analysis(clamped, '心情轻快，话也多了些。', threshold, NOW, CONFIG, '')
check(done['emotional_offset']['direction'] == 'relaxed' and done['emotional_offset']['intensity'] < 1.5,
      '温和强度: %s（阈值 %.2f）' % (done['emotional_offset'], threshold))
raw = alter.complete_alter_analysis(PROD, 'x', threshold, NOW, CONFIG, '')
check(raw['emotional_offset']['intensity'] == 2.0, '对照：不夹紧会直接顶到上限 2.0')
print('T2 积压桶夹紧 ✓')

print(f'test_alter_trigger: {ok} checks passed')
