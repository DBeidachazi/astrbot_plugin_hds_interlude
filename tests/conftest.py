"""上游测试套件的运行环境。

本仓库在上游之上加了几个默认开启、读 plugin_data 下 JSON 配置的本地扩展（现实日历、
生活钩子、长线剧情）。配置文件缺失时它们按默认值开启，会改变上游用例断言的行为
（日程预排被日历接管、interval 多出 realCalendar 字段）。上游用例只验证上游行为，
这里把三个扩展指向「关闭」的临时配置；扩展本身由 tests_local/ 覆盖。
"""

import json
import os
import tempfile

_DIR = tempfile.mkdtemp(prefix='hdsi-upstream-tests-')
for _env, _name in (
    ('HDSI_REAL_CALENDAR_CONFIG', 'real_calendar.json'),
    ('HDSI_LIFE_HOOKS_CONFIG', 'life_hooks.json'),
    ('HDSI_STORY_ARCS_CONFIG', 'story_arcs.json'),
):
    _path = os.path.join(_DIR, _name)
    with open(_path, 'w', encoding='utf-8') as _file:
        json.dump({'enabled': False}, _file)
    os.environ.setdefault(_env, _path)
