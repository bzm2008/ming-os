"""按缩进正确判定 preset 内某行的 disabled 状态。
坑：`- id:` 前面的空格属于「列表项缩进」，同行键（name/disabled）比它多 2 空格。"""
import re, sys
from pathlib import Path

def state(seg, rid):
    m = re.search(rf"^( *)- id: {re.escape(rid)}\s*$", seg, re.M)
    if not m:
        return "不挂"
    dash = len(m.group(1)); key = dash + 2
    lines = seg[m.end():].split("\n")[1:]
    for l in lines:
        if not l.strip():
            continue
        cur = len(l) - len(l.lstrip())
        if cur < key:
            break
        if cur > key:
            continue
        if re.match(r"^ *- id: ", l):
            break
        d = re.match(r"^ *disabled:\s*(\S+)", l)
        if d:
            return d.group(1)
        if re.match(r"^ *config:\s*$", l):
            break
    return "启用"

s = Path(sys.argv[1]).read_text(encoding="utf-8")
bounds = [("办公模式(standard)", "- id: preset-standard", "- id: preset-ptc"),
          ("开发模式(ptc)", "- id: preset-ptc", "- id: preset-minimal"),
          ("学习模式(minimal)", "- id: preset-minimal", None)]
rows = ["tool-subagent-codex","tool-subagent-claude-code","tool-ralph",
        "workflow-ptc","tool-workflow","tool-presentation","tool-fs","tool-bash"]
for name, a, b in bounds:
    i = s.index(a)
    j = s.index(b) if b else len(s)
    seg = s[i:j]
    print(f"=== {name} ===")
    print("   " + ", ".join(f"{r}={state(seg, r)}" for r in rows))
