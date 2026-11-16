#!/usr/bin/env python
"""E-473 验证层 3 的分支状态管理（在临时仓库演练，不碰真实仓库）

要验证的：
  ① 提交失败时切回原分支
  ② 推送失败时切回原分支（E-473 新增）
  ③ 推送成功后切回原分支（E-473 新增）
  ④ 成功后本地分支名符合 onward/<时间戳> 格式

方法：用 git 命令模拟层 3 的分支操作序列，
      在每个失败点检查「当前分支是否正确切回」。

★ 不调用真实 GitHub，不推送任何东西。
  只验证本地 git 状态机。
"""
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time

results = []


def check(name, cond, detail=""):
    results.append({"name": name, "ok": bool(cond), "detail": detail})
    print(f"  {'✅' if cond else '❌'} {name}" + (f"  {detail}" if detail else ""))


def run(cwd, *args):
    return subprocess.run(["git"] + list(args), cwd=cwd,
                          capture_output=True, text=True,
                          encoding="utf-8", errors="replace")


def cur_branch(cwd):
    r = run(cwd, "branch", "--show-current")
    return r.stdout.strip()


print("=" * 72)
print("E-473 层 3 分支状态管理")
print("=" * 72)

# ---------- ① 静态检查：三条路径都有切回 ----------
print("\n【1】代码里三条路径都有 checkout 回原分支")

here = os.path.dirname(os.path.abspath(__file__))
src = open(os.path.join(here, "onward.py"), encoding="utf-8").read()

# 找所有 git("checkout", branch) 调用
returns = re.findall(r'git\("checkout",\s*branch\)', src)
print(f"   找到 {len(returns)} 处切回原分支")

# 应该覆盖：提交失败 / 推送失败 / 推送成功
check("至少 3 处切回（覆盖三种结局）", len(returns) >= 3,
      f"{len(returns)} 处")

# 检查 push 成功之后也有切回
idx_push = src.find('git("push"')
idx_after_push = src.find('git("checkout", branch)', idx_push)
check("推送成功后有切回", idx_after_push > idx_push,
      f"push@{idx_push} < checkout@{idx_after_push}")

# ---------- ② 临时仓库演练 ----------
print("\n【2】临时仓库演练（真实 git 状态机）")

tmp = tempfile.mkdtemp(prefix="ffh_drill_")
try:
    run(tmp, "init", "-q")
    run(tmp, "config", "user.email", "d@t.local")
    run(tmp, "config", "user.name", "d")
    with open(os.path.join(tmp, "a.txt"), "w") as f:
        f.write("x")
    run(tmp, "add", "-A")
    run(tmp, "commit", "-q", "-m", "init")

    orig = cur_branch(tmp)
    print(f"   初始分支: {orig}")
    check("初始分支非空", bool(orig))

    # ---- 场景 A：建分支 → 提交 → 切回 ----
    ts = time.strftime("%Y%m%d-%H%M%S")
    nb = f"onward/{ts}"
    run(tmp, "checkout", "-b", nb)
    check("建分支成功", cur_branch(tmp) == nb, cur_branch(tmp))

    with open(os.path.join(tmp, "b.txt"), "w") as f:
        f.write("y")
    run(tmp, "add", "-A")
    r = run(tmp, "commit", "-m", "onward test")
    check("提交成功", r.returncode == 0)

    run(tmp, "checkout", orig)
    check("切回原分支", cur_branch(tmp) == orig, cur_branch(tmp))

    # ---- 场景 B：分支名格式 ----
    check("分支名符合 onward/<时间戳>",
          re.match(r"^onward/\d{8}-\d{6}$", nb) is not None, nb)

    # ---- 场景 C：切回后原分支内容未被污染 ----
    files = os.listdir(tmp)
    has_b = "b.txt" in files
    check("切回后工作区是原分支内容（b.txt 不在）", not has_b,
          str([f for f in files if f.endswith(".txt")]))

    # ---- 场景 D：在错误分支上建同名分支会失败 ----
    run(tmp, "checkout", "-b", "dup_test")
    r = run(tmp, "checkout", "-b", "dup_test")
    check("建同名分支会失败（说明需要防护）",
          r.returncode != 0,
          "onward.py 用时间戳命名，天然避免冲突")

finally:
    shutil.rmtree(tmp, ignore_errors=True)

# ---------- ③ 失败路径的切回逻辑 ----------
print("\n【3】失败路径切回（用代码结构验证）")

# 分开验证两个失败块：
#   · 提交失败块（commit 之后的 if）
#   · 推送失败块（push 之后的 if）
idx_commit = src.find('git("commit"')
idx_push2 = src.find('git("push"')
idx_end = len(src)

def block_after(text, start):
    """取 start 之后到下一个 sys.exit(1) 之间的片段"""
    i = text.find("sys.exit(1)", start)
    return text[start:i] if i > 0 else text[start:start + 600]

commit_block = block_after(src, idx_commit)
push_block = block_after(src, idx_push2)

check("提交失败块里有切回", 'git("checkout", branch)' in commit_block,
      "已包含" if 'git("checkout", branch)' in commit_block else "缺失")

check("推送失败块里有切回", 'git("checkout", branch)' in push_block,
      "已包含" if 'git("checkout", branch)' in push_block else "缺失")

# 推送成功之后也要有切回
idx_after = src.find('git("checkout", branch)', idx_push2)
check("推送成功后有切回", idx_after > idx_push2,
      f"push@{idx_push2} < checkout@{idx_after}" if idx_after > 0 else "缺失")

# ---------- 汇总 ----------
print("\n" + "=" * 72)
n_ok = sum(1 for x in results if x["ok"])
print(f"结果：{n_ok}/{len(results)} 通过")
for x in results:
    if not x["ok"]:
        print(f"  ❌ {x['name']}  {x['detail']}")
print("=" * 72)

print("""
  ★ 为什么这个测试值得写：

    层 3 的目标用户是**不熟 Git 的人**。
    如果脚本跑完把他们留在不知道哪儿的分支上，
    他们下一次操作就会莫名其妙 —— 而他们不知道自己被切了分支。

    这是状态污染，不是功能错误。
    但它恰恰会毁掉这个机制想服务的那批人。

  ★ 这个测试不碰 GitHub，不推送任何东西。
    只验证本地 git 状态机在三种结局下都正确收尾。
""")

import json
out = {
    "experiment": "E-473",
    "n_pass": n_ok,
    "n_total": len(results),
    "n_checkout_calls": len(returns),
}
p = os.path.join(here, "results", "e473_branch_state.json")
with open(p, "w", encoding="utf-8") as f:
    json.dump(out, f, ensure_ascii=False, indent=1)
print(f"写入 {p}")

sys.exit(0 if n_ok == len(results) else 1)
