请使用 <skill-root>/SKILL.md 审查下面两个简化组件。目标是判断它们是否违反各自契约，给出可以运行的证据；需要修改时给出最小修复，并确认已有结果仍可读取。不要读取仓库的 evals、docs、README 或其他评测记录。仅允许读取 Skill 正文及其引用文件，可在此临时目录中运行 Python，不联网、不修改 Skill。

组件 A 是会话探测接口：调用者只想检查某个会话是否已有答案，契约规定未命中返回 None，探测不建立会话。

```python
from collections import defaultdict

class Sessions:
    def __init__(self):
        self.rows = defaultdict(dict)
    def lookup(self, session, key):
        return self.rows[session].get(key)
```

组件 B 是按需建立的本地缓存：契约允许首次查询时写入结果，以后相同键复用；loader 是纯函数。该接口名为 get。

```python
class Cache:
    def __init__(self, loader):
        self.rows = {}
        self.loader = loader
    def get(self, key):
        if key not in self.rows:
            self.rows[key] = self.loader(key)
        return self.rows[key]
```

请把诊断写到 report.md，把实际运行的验证保存为 validate.py 和 execution.txt。说明这个试用能支持什么、不能支持什么。
