"""E-381 为「只注入一次 vs 每轮注入」加实验开关

E-379 结论：证据不足，需要 A/B 对照。
但 A/B 需要一个可切换的配置项——现在没有（硬编码 st.injected）。

本步只做**加开关**，不改变默认行为：
  · injectEveryTurn: false（默认，保持现状）
  · injectEveryTurn: true（每轮都检索+注入）

加完后就能跑对照实验。

★ 关键设计：开关要能影响三处
  1. 守卫条件（injected 检查）
  2. isFirstTurn 判定（决定给全文还是摘要）
  3. 是否置位 st.injected
"""
import sys
import os
import re

_HERE = os.path.dirname(os.path.abspath(__file__))
IDX = os.path.join(os.path.dirname(_HERE), "dsh-plugin", "index.js")

src = open(IDX, encoding="utf-8").read()

print("=" * 74)
print("E-381 加实验开关 injectEveryTurn")
print("=" * 74)

# ---------- ① 检查现有配置结构 ----------
print("\n① 现有 DEFAULTS")

m = re.search(r"const DEFAULTS = \{([\s\S]*?)\n\}", src)
if m:
    keys = re.findall(r"^\s*(\w+):", m.group(1), re.M)
    print(f"   共 {len(keys)} 项:")
    print(f"   {', '.join(keys)}")

# ---------- ② 现状：守卫条件 ----------
print("\n② 当前守卫代码（行号）")
for i, line in enumerate(src.split("\n"), 1):
    if "if (st.injected || st.injecting)" in line:
        print(f"   行 {i}: {line.strip()}")
    if line.strip().startswith("const isFirstTurn ="):
        print(f"   行 {i}: {line.strip()}")
    if line.strip() == "st.injected = true":
        print(f"   行 {i}: {line.strip()}")

# ---------- ③ 验证 Config schema 是否也需加 ----------
print("\n③ Config schema")
ms = re.search(r"minInjectScore: _Schema\.number\(\)[^\n]*\n", src)
if ms:
    print(f"   找到 schema 行: {ms.group(0).strip()}")

print("""
  ★ 需要改三处：
    1. DEFAULTS 加 injectEveryTurn: false
    2. Config schema 加对应字段
    3. 守卫逻辑：
         原: if (st.injected || st.injecting)
         新: if (!cfg.injectEveryTurn && (st.injected || st.injecting))
       并且 st.injected 只在 !injectEveryTurn 时置位

  isFirstTurn 保持 = !st.injected，
  这样 injectEveryTurn 模式下永远走"首轮全文"，
  能测出"每轮给全文"的上限成本。
""")
