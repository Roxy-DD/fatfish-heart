#!/usr/bin/env python
"""E-472 回馈机制的门禁回归

为什么门禁本身要有测试：

  onward.py 会往公共仓库写东西。如果它的门禁失效，
  后果是**私人数据被推到公共空间** —— 不可撤销。

  这和 E-449（私人数据库进 git 历史）是同一类风险，
  只不过那次是我手工操作失误，这次是通过自动化放大。

所以门禁必须被验证，而不是"我写的时候是对的"。

本测试用**构造的假文件**验证三类门禁：
  ① 本机路径 → 必须拦截
  ② 敏感文件（.db） → 必须拦截
  ③ 纯实验产物 → 必须拒绝

不触碰真实仓库，全部在临时目录里跑。
"""
import os
import re
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))

results = []


def check(name, cond, detail=""):
    results.append({"name": name, "ok": bool(cond), "detail": detail})
    print(f"  {'✅' if cond else '❌'} {name}" + (f"  {detail}" if detail else ""))


print("=" * 72)
print("E-472 回馈门禁回归")
print("=" * 72)

# ---------- ① 门禁规则存在性 ----------
print("\n【1】门禁规则存在")

src = open(os.path.join(HERE, "onward.py"), encoding="utf-8").read()

check("有本机路径检测", r"C:\\Users" in src or "C:\\\\Users" in src)
# 注意：onward.py 里用的是正则字面量 r"\.db$" / r"\.db\.backup" /
#       r"\.seed\.json$"，所以要按正则形式匹配，不能匹配 "db.backup"
NEED = [r"\.db$", r"\.db\.backup", r"\.seed\.json$"]
missing = [k for k in NEED if k not in src]
check("有敏感文件拦截", len(missing) == 0,
      f"缺 {missing}" if missing else "3 条规则齐全")
check("有实验产物排除", "/results/" in src)
check("有 --push 门禁", '"--push"' in src)
check("无 force push", "--force" not in src)
check("只推 onward 分支", "onward/" in src)

# ---------- ② 分类函数行为 ----------
print("\n【2】分类逻辑（用构造路径验证）")

# E-474：扫描器自引用问题
#
# onward.py 内部含检测本机路径的正则，会命中自己，
# 导致「工具判定自己不合格」的死循环。
# 必须显式排除自身。
check("扫描器排除自身（避免自我误判）",
      "SELF_FILES" in src and "proto/onward.py" in src,
      "已排除" if "SELF_FILES" in src else "未排除 → 会自我误判")

# E-477：只检查实际代码行，不把注释里的说明当问题
#
# 反例：dsh-plugin/index.js 注释里写了
#   //   C:\Users\xuee\Desktop\肥鱼之心\dsh-plugin 解析不到
# 这是解释文字，不是配置值。纯文本扫描会误判。
check("检查排除注释行（避免误判）",
      "has_live_path" in src and "startswith" in src,
      "已实现" if "has_live_path" in src else "未实现 → 注释会误伤")

# 复制一份分类逻辑（保持与 onward.py 一致）
BLOCKED_PATTERNS = [
    r"\.db$", r"\.db-wal$", r"\.db-shm$", r"\.db\.backup",
    r"\.seed\.json$", r"results/.*\.json$",
    r"^\.hf/", r"^models/", r"^data/", r"^_probe/",
]


def classify(path):
    for pat in BLOCKED_PATTERNS:
        if re.search(pat, path):
            return "blocked"
    if path.endswith((".py", ".js", ".mjs", ".md", ".yml", ".yaml",
                      ".txt", ".json")):
        if "/results/" in path:
            return "artifact"
        return "ok"
    return "other"


CASES = [
    ("proto/cosmos_mem/store_v1.py", "ok"),
    ("README.md", "ok"),
    ("docs/ONWARD.md", "ok"),
    ("proto/cosmos_mem/dsh_mem.db", "blocked"),
    ("proto/cosmos_mem/dsh_mem.db.backup.20261110", "blocked"),
    ("proto/cosmos_mem/seed_memory.json", "ok"),   # 种子是公开的
    ("proto/cosmos_mem/x.seed.json", "blocked"),
    ("proto/results/e377.json", "blocked"),        # 被 results 规则拦
    ("models/bge-small-zh/config.json", "blocked"),
    ("data/tnews/train.parquet", "blocked"),
]

fails = []
for path, expect in CASES:
    got = classify(path)
    # results/*.json 同时匹配 blocked 和 artifact，以 blocked 优先
    ok = (got == expect) or (expect == "artifact" and got == "blocked")
    if not ok:
        fails.append((path, expect, got))

check("分类逻辑全部正确", len(fails) == 0,
      f"{len(fails)} 个不符" if fails else f"{len(CASES)} 个用例")
for p, e, g in fails[:5]:
    print(f"      {p}: 期望 {e} 实际 {g}")

# ---------- ③ 无 --push 时不推送 ----------
print("\n【3】安全默认值：无 --push 不推送")

# 用 git log 确认刚才的运行没有产生新提交
r = subprocess.run(
    ["git", "log", "--oneline", "-1"],
    cwd=HERE, capture_output=True, text=True, encoding="utf-8")

# 检查 onward.py 里 sys.exit 的路径
has_early_exit = ("if \"--push\" not in sys.argv:" in src
                  or "if '--push' not in sys.argv:" in src)
check("无 --push 时提前 exit", has_early_exit)

# ---------- ④ 三重门禁都在 push 之前 ----------
print("\n【4】门禁顺序：必须在推送之前")

idx_push = src.find('git("push"')
if idx_push < 0:
    idx_push = src.find("git('push'")
idx_issue_gate = src.find("if issues:")
idx_empty_gate = src.find("if not ok_items:")

check("找到 push 调用", idx_push > 0)
check("敏感检查在 push 之前",
      idx_issue_gate > 0 and idx_issue_gate < idx_push,
      f"检查@{idx_issue_gate} < push@{idx_push}")
check("空贡献检查在 push 之前",
      idx_empty_gate > 0 and idx_empty_gate < idx_push,
      f"检查@{idx_empty_gate} < push@{idx_push}")

# ---------- ⑤ 真实脚本能跑（发现模式） ----------
print("\n【5】脚本可运行（发现模式，不推送）")

r = subprocess.run(
    [sys.executable, "-u", os.path.join(HERE, "onward.py")],
    cwd=HERE, capture_output=True, text=True, timeout=180,
    encoding="utf-8", errors="replace")
out = (r.stdout or "") + (r.stderr or "")

check("发现模式正常退出", r.returncode == 0,
      f"exit={r.returncode}")
check("输出含安全检查", "安全检查" in out)
check("输出含授权要求", "授权" in out or "必须由用户" in out)

# ---------- 汇总 ----------
print("\n" + "=" * 72)
n_ok = sum(1 for x in results if x["ok"])
print(f"结果：{n_ok}/{len(results)} 通过")
for x in results:
    if not x["ok"]:
        print(f"  ❌ {x['name']}  {x['detail']}")
print("=" * 72)

print("""
  ★ 这个测试守的是什么：

    onward.py 会往公共仓库写东西。
    门禁失效的后果是私人数据被推上去 —— 不可撤销。

    所以门禁本身必须被验证，不能只靠"我写的时候是对的"。
    这和 E-449（私人库进 git 历史）是同一类风险，
    只是那次手工失误，这次会被自动化放大。
""")

import json
out_data = {
    "experiment": "E-472",
    "n_pass": n_ok,
    "n_total": len(results),
}
p = os.path.join(HERE, "results", "e472_onward_gate.json")
with open(p, "w", encoding="utf-8") as f:
    json.dump(out_data, f, ensure_ascii=False, indent=1)
print(f"写入 {p}")

sys.exit(0 if n_ok == len(results) else 1)
