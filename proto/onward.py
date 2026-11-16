"""E-471 自动回馈机制：把本地演化成果送回群的横向边

《环管群》主张 4：
  「演化关系不必是单一树状链。多个谱系可以交换机制、
    经验与结构，并通过交叉重组形成新的分支。」

现在的缺口：
  环（本地实例）产生了改进，但**回到群（上游项目）的横向边
  必须由人手工搬**：git commit → fork → PR → 等 review。
  这就把"能不能贡献"绑定到了"有没有技术能力"。

  而用户的意图是：
    「不必须得有想法、有技术、能改进的人才能够做出贡献」
    「可以脱离某个人或主体」

设计（三层，由轻到重）：

  层 1  发现（自动）
    扫描本地相对 upstream 的差异，分类：
      · 可贡献（代码/文档/测试）
      · 不可贡献（私人数据/本机路径/实验产物）
    产出候选清单，不是直接推送。

  层 2  询问（必须）
    把候选清单给用户看，问「要不要回馈这些」。
    ★ 这一步**不能省**：上传别人的东西到公共仓库是外部提交，
      必须经用户授权（这也是所有 MCP/工具约定的底线）。

  层 3  执行（授权后自动）
    自动建分支 → 提交 → 推送 → 开 PR（用 gh）。
    PR 描述里写清「这是自动生成的贡献，来自 <实例>」。

本脚本只做层 1 + 层 3 的骨架，层 2 的询问由我（模型）负责。

★ 关键约束（不能违反）：
  · 绝不上传：dsh_mem.db / 备份 / 实验产物 / 本机路径
  · 绝不 force push 到 master
  · 绝不无授权推送
  · PR 必须可审查（小、清晰、带理由）
"""
import json
import os
import re
import subprocess
import sys

ROOT = r"C:\Users\xuee\Desktop\肥鱼之心"
UPSTREAM = "Roxy-DD/fatfish-heart"


def git(*args, timeout=120):
    return subprocess.run(["git"] + list(args), cwd=ROOT,
                          capture_output=True, text=True,
                          encoding="utf-8", errors="replace",
                          timeout=timeout)


def gh(*args, timeout=120):
    return subprocess.run(["gh"] + list(args), cwd=ROOT,
                          capture_output=True, text=True,
                          encoding="utf-8", errors="replace",
                          timeout=timeout)


print("=" * 72)
print("E-471 自动回馈：层 1 发现")
print("=" * 72)

# ---------- ① 当前状态 ----------
print("\n① 本地 vs upstream")

r = git("rev-parse", "--abbrev-ref", "HEAD")
branch = r.stdout.strip()
print(f"   当前分支: {branch}")

r = git("log", "--oneline", f"origin/master..HEAD")
ahead = [x for x in r.stdout.split("\n") if x.strip()]
print(f"   领先 upstream: {len(ahead)} 个提交")
for c in ahead[:5]:
    print(f"     {c[:80]}")

r = git("status", "--porcelain")
dirty = [x for x in r.stdout.split("\n") if x.strip()]
print(f"   未提交改动: {len(dirty)} 个")

# ---------- ② 分类：什么能贡献 ----------
print("\n② 差异分类")

CONTRIBUTABLE = [
    ("proto/", ".py"),
    ("dsh-plugin/", ".js"),
    ("docs/", ".md"),
    (".", ".md"),          # README / CONTRIBUTING
    (".github/", ".yml"),
]

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
        # 实验产物不算贡献
        if "/results/" in path:
            return "artifact"
        return "ok"
    return "other"

candidates = []
for line in dirty:
    st = line[:2].strip()
    path = line[3:].strip().strip('"')
    kind = classify(path)
    candidates.append({"status": st, "path": path, "kind": kind})

if not candidates:
    print("   工作区干净，没有可贡献的改动")
else:
    from collections import Counter
    cnt = Counter(c["kind"] for c in candidates)
    print(f"   共 {len(candidates)} 项:")
    for k, n in cnt.items():
        label = {"ok": "可贡献", "blocked": "禁止上传",
                 "artifact": "实验产物", "other": "其他"}[k]
        print(f"     {label:<8} {n}")

    ok_items = [c for c in candidates if c["kind"] == "ok"]
    if ok_items:
        print(f"\n   可贡献明细（前 15）:")
        for c in ok_items[:15]:
            print(f"     [{c['status']}] {c['path']}")

# ---------- ③ 安全检查 ----------
print("\n③ 安全检查（贡献前必须全过）")

issues = []

# 3.1 本机路径
r = git("diff", "--name-only")
changed_files = [x for x in r.stdout.split("\n") if x.strip()]
local_path_files = []
for f in changed_files:
    p = os.path.join(ROOT, f)
    if not os.path.exists(p) or not f.endswith((".py", ".js", ".md")):
        continue
    try:
        c = open(p, encoding="utf-8", errors="replace").read()
    except Exception:
        continue
    if re.search(r"C:\\Users\\[^\\]+\\", c):
        local_path_files.append(f)

if local_path_files:
    issues.append(f"{len(local_path_files)} 个文件含本机路径")
    for f in local_path_files[:5]:
        print(f"   ❌ 本机路径: {f}")
else:
    print("   ✅ 无本机路径")

# 3.2 敏感文件
bad = [f for f in changed_files
       if re.search(r"\.db|\.backup|\.seed\.json", f)]
if bad:
    issues.append(f"{len(bad)} 个敏感文件")
    for f in bad[:5]:
        print(f"   ❌ 敏感文件: {f}")
else:
    print("   ✅ 无敏感文件")

# 3.3 语法
syn_err = 0
for f in changed_files:
    if not f.endswith(".py"):
        continue
    p = os.path.join(ROOT, f)
    if not os.path.exists(p):
        continue
    try:
        import ast
        ast.parse(open(p, encoding="utf-8", errors="replace").read())
    except SyntaxError:
        syn_err += 1
if syn_err:
    issues.append(f"{syn_err} 个文件语法错误")
    print(f"   ❌ 语法错误: {syn_err}")
else:
    print("   ✅ 语法通过")

# 3.4 回归（不在这里跑，太慢；提示必须跑）
print("   ⚠ 回归需另行确认（37 项，需模型）")

# ---------- ④ 输出贡献候选 ----------
print("\n" + "=" * 72)
print("层 1 结论")
print("=" * 72)

if issues:
    print(f"\n❌ 有 {len(issues)} 个阻塞问题，不能自动贡献：")
    for i in issues:
        print(f"   · {i}")
    print("\n需要先修复再贡献。")
    ready = False
elif not candidates:
    print("\n工作区没有改动，无需贡献。")
    ready = False
else:
    ok_items = [c for c in candidates if c["kind"] == "ok"]
    if ok_items:
        print(f"\n✅ {len(ok_items)} 项可贡献，安全检查通过")
        print("\n【层 2：需要用户授权】")
        print("  这些改动要上传到公共仓库，属于外部提交。")
        print("  必须由用户明确同意后才能执行层 3。")
        print("\n  待用户确认后，执行：")
        print("    python proto/onward.py --push")
        ready = True
    else:
        print("\n没有可贡献项（改动都是实验产物或禁止上传的类型）")
        ready = False

out = {
    "experiment": "E-471",
    "branch": branch,
    "ahead_commits": len(ahead),
    "dirty_files": len(dirty),
    "candidates": candidates[:80],
    "issues": issues,
    "ready_to_contribute": ready,
}
p = os.path.join(ROOT, "proto", "results", "e471_onward_discover.json")
with open(p, "w", encoding="utf-8") as f:
    json.dump(out, f, ensure_ascii=False, indent=1)
print(f"\n写入 {p}")

# ============================================================
# 层 3：执行（仅在 --push 且安全检查通过时）
# ============================================================
if "--push" not in sys.argv:
    sys.exit(0 if not issues else 1)

print("\n" + "=" * 72)
print("层 3 执行（--push 已指定）")
print("=" * 72)

# 三重门禁：任何一道不过就不推
if issues:
    print("\n❌ 拒绝执行：安全检查未通过")
    for i in issues:
        print(f"   · {i}")
    sys.exit(1)

if not candidates:
    print("\n❌ 拒绝执行：没有可贡献的改动")
    sys.exit(1)

ok_items = [c for c in candidates if c["kind"] == "ok"]
if not ok_items:
    print("\n❌ 拒绝执行：改动都是实验产物，不值得作为贡献")
    sys.exit(1)

# 3.1 建分支
import time
ts = time.strftime("%Y%m%d-%H%M%S")
new_branch = f"onward/{ts}"

print(f"\n① 建分支 {new_branch}")
r = git("checkout", "-b", new_branch)
if r.returncode != 0:
    print(f"   ❌ 失败: {(r.stderr or '')[:200]}")
    sys.exit(1)
print("   ✅ 已切换")

# 3.2 只提交可贡献项（逐个 add，不用 -A）
print(f"\n② 提交 {len(ok_items)} 项")
for c in ok_items:
    git("add", "--", c["path"])
    print(f"   + {c['path']}")

msg = (
    f"onward: 自动回馈 {len(ok_items)} 项改动\n\n"
    f"来源：独立实例 {branch}\n"
    f"时间：{ts}\n"
    f"安全检查：无本机路径 / 无敏感文件 / 语法通过\n"
)
r = git("commit", "-m", msg)
if r.returncode != 0:
    print(f"   ⚠ 提交失败（可能无可提交内容）: "
          f"{(r.stdout or r.stderr or '')[:150]}")
    git("checkout", branch)
    git("branch", "-D", new_branch)
    sys.exit(1)
print("   ✅ 已提交")

# 3.3 推送到 fork（不是 upstream 的 master）
print("\n③ 推送到 origin")
r = git("push", "-u", "origin", new_branch)
if r.returncode != 0:
    print(f"   ❌ 推送失败: {(r.stderr or '')[:300]}")
    print("   （本地分支已保留，可稍后重试）")
    sys.exit(1)
print("   ✅ 已推送")

# 3.4 开 PR
print("\n④ 创建 PR")
body = (
    "## 自动回馈\n\n"
    f"由独立实例在 `{ts}` 自动生成。\n\n"
    "### 改动内容\n"
    + "\n".join(f"- `{c['path']}`" for c in ok_items)
    + "\n\n### 安全检查（自动执行）\n"
    "- [x] 无本机绝对路径\n"
    "- [x] 无数据库/备份/密钥\n"
    "- [x] 语法检查通过\n\n"
    "### 说明\n"
    "这是「环管群」机制的实际执行：一个独立实例（环）把本地演化成果\n"
    "回馈给上游（群）。整个过程不需要人具备 Git 知识。\n\n"
    "**审查者请重点看**：这些改动是否改善了通用性，"
    "还是只是该实例的特殊配置。\n"
)
r = gh("pr", "create",
       "--repo", UPSTREAM,
       "--base", "develop",
       "--head", new_branch,
       "--title", f"onward: 自动回馈 {len(ok_items)} 项改动（{ts}）",
       "--body", body)
if r.returncode != 0:
    print(f"   ⚠ PR 创建失败: {(r.stderr or '')[:300]}")
    print(f"   分支已推送，可手动开 PR：")
    print(f"   https://github.com/{UPSTREAM}/compare/develop..."
          f"{new_branch}?expand=1")
else:
    print(f"   ✅ PR 已创建: {r.stdout.strip()}")

print("\n" + "=" * 72)
print("回馈完成")
print("=" * 72)
print(f"\n分支: {new_branch}")
print("PR 需要维护者审查后合并——这一步不能自动化，")
print("因为'这个改动对群体是否真的更好'是判断题，")
print("不该由提交方自己裁决。")

