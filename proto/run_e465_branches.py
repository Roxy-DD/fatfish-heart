"""E-465 建立开源分支体系（正规项目标准）

背景：
  当前只有一个 master 分支，所有改动直接堆在上面。
  这不是正规开源项目的做法 —— 无法：
    · 让外部贡献者安全提交（没有 develop 缓冲）
    · 隔离未完成的实验（这个项目实验很多）
    · 维护旧版本（v0.x 稳定线）

标准分支模型（GitHub Flow + Git Flow 精简版）：

  master        受保护，只接受 PR。代表**可发布状态**。
  develop       日常集成。所有 feature 合到这里。
  feat/*        单个功能/实验。从 develop 切，合回 develop。
  fix/*         缺陷修复。从 develop 切。
  release/*     发布准备（版本号、文档冻结）。
  docs/*        纯文档改动，可绕过 develop 直接 PR 到 master。

★ 这个项目的特殊之处：
  实验非常多（E-1 到 E-462），每个实验都有 run_e*.py + results/*.json。
  如果每个实验一个分支，分支会爆炸。

  所以规则应该是：
    · 探索性实验 → 直接在 develop 上做（结果进 results/，可追溯）
    · 结构性改动（改红线机制、改架构）→ 开 feat/ 分支
    · 发版准备 → release/

本脚本只**创建分支**，不改代码。
"""
import subprocess
import json

ROOT = r"C:\Users\xuee\Desktop\肥鱼之心"


def git(*args, check=False):
    r = subprocess.run(["git"] + list(args), cwd=ROOT,
                       capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    if check and r.returncode != 0:
        raise RuntimeError(r.stderr[:300])
    return r


print("=" * 72)
print("建立分支体系")
print("=" * 72)

# ---------- 当前状态 ----------
cur = git("branch", "--show-current").stdout.strip()
print(f"当前分支: {cur}")

# ---------- 创建 develop ----------
print("\n① 创建 develop（日常集成线）")

r = git("rev-parse", "--verify", "develop")
if r.returncode == 0:
    print("   develop 已存在，跳过")
else:
    r = git("branch", "develop", check=False)
    if r.returncode == 0:
        print("   ✅ 已创建 develop")
    else:
        print(f"   ❌ 失败: {r.stderr[:150]}")

# ---------- 创建 release/v0.1 ----------
print("\n② 创建 release/v0.1（当前版本稳定线）")

r = git("rev-parse", "--verify", "release/v0.1")
if r.returncode == 0:
    print("   release/v0.1 已存在，跳过")
else:
    r = git("branch", "release/v0.1", check=False)
    if r.returncode == 0:
        print("   ✅ 已创建 release/v0.1")
    else:
        print(f"   ❌ 失败: {r.stderr[:150]}")

# ---------- 列出结果 ----------
print("\n③ 当前所有分支")
r = git("branch", "-a")
print(r.stdout)

# ---------- 输出计划 ----------
print("=" * 72)
print("分支使用计划")
print("=" * 72)

PLAN = """
┌──────────────┬─────────────────────────────────────────────┐
│ 分支          │ 用途                                        │
├──────────────┼─────────────────────────────────────────────┤
│ master       │ 只放**可发布**状态。只接受 PR。              │
│              │ 每次合入都打 tag（v0.1.0 / v0.2.0 ...）      │
├──────────────┼─────────────────────────────────────────────┤
│ develop      │ 日常集成。feature 合进这里。                 │
│              │ 允许暂时不完美，CI 通过即可。                │
├──────────────┼─────────────────────────────────────────────┤
│ feat/<名字>  │ 单个结构性改动。从 develop 切，合回 develop。│
│              │ 例：feat/rename-fatfish                     │
│              │     feat/semantic-inject                    │
├──────────────┼─────────────────────────────────────────────┤
│ fix/<名字>   │ 缺陷修复。从 develop 切。                    │
│              │ 例：fix/path-portability                    │
├──────────────┼─────────────────────────────────────────────┤
│ release/vX.Y │ 发布准备：版本号、文档冻结、最后回归。        │
│              │ 完成合入 master 并打 tag，然后删除。         │
├──────────────┼─────────────────────────────────────────────┤
│ docs/<名字>  │ 纯文档。可直接 PR 到 master（跳过 develop）。│
└──────────────┴─────────────────────────────────────────────┘

【这个项目的特殊规则】

实验太多（E-1 ~ E-462），不能每个实验一个分支，否则分支爆炸。
所以：

  ① 探索性实验（run_e*.py + results/*.json）
     → 直接在 develop 上做
     → 理由：结果本身就是可追溯的证据，不需要分支隔离
     → 前提：不碰红线机制

  ② 结构性改动（改存储层 / 改红线 / 改架构）
     → 必须开 feat/ 分支
     → 必须过 36 项回归才合并
     → 例：E-449 历史重写就该开 feat/purge-private-data

  ③ 发版
     → release/vX.Y 冻结 → 回归全绿 → 合 master → 打 tag

【立即可做的整理】

  当前 master 上有 462 个实验的提交历史，很杂。
  但**不建议**重写历史来整理（风险高、收益低）。
  从 v0.1.0 开始，新提交按上述规则走即可。

  下一步动作：
    1. 把 master 当前状态同步到 develop
    2. 在 GitHub 上给 master 设保护规则：
       · 禁止直接 push
       · 必须 PR + CI 通过
       · 必须 1 个 approve（自己项目可放宽）
    3. 写 CONTRIBUTING.md 说明这些规则
"""

print(PLAN)

out = {
    "experiment": "E-465",
    "branches": ["master", "develop", "release/v0.1"],
    "model": "GitHub Flow + 精简 Git Flow",
}
p = r"C:\Users\xuee\Desktop\肥鱼之心\proto\results\e465_branches.json"
with open(p, "w", encoding="utf-8") as f:
    json.dump(out, f, ensure_ascii=False, indent=1)
print(f"写入 {p}")
