"""E-257 逐个判断：5 处模块级可变状态，哪些真有风险？

E-256 扫描出 5 处模块级可变状态，但**不能一概而论**：
  · 有些状态跨重载保持是**正确的**（比如内核进程池，重启进程反而浪费）
  · 有些状态跨重载保持是**危险的**（E-254 的 REGISTERED_ROUTES 就是）

判断标准（唯一标准）：**宿主重建后，这个状态是否仍然有效？**

逐个分析：
  sessions      会话状态，sid -> {prewarm, processed}
                宿主重建 → 旧 sid 不再存在 → 保留无害（查不到就用不到）
                → 低风险（最多是内存占用）

  injectHistory 注入历史（内存数组）
                宿主重建 → 历史还在，用于 UI 展示，无害
                → 低风险

  scanCursor    扫描游标，sid -> lastSeq
                宿主重建 → 旧 sid 的游标无用，新的会重置
                → 低风险

  kernelPools   内核进程池，dbPath -> {proc, ...}
                宿主重建 → 旧进程还在跑，复用可省启动时间
                **这是有意为之的优化**
                → 但要注意：如果进程已崩溃，复用会失败

  REGISTERED_BY_CTX  WeakMap
                已经按 ctx 隔离，ctx 换就新建
                → 已修正，无风险（扫描器误报）

本实验验证：kernelPools 是否有"进程已死但仍复用"的问题。
"""
import sys
import os
import json
import re

_HERE = os.path.dirname(os.path.abspath(__file__))
plugin = os.path.join(os.path.dirname(_HERE), "dsh-plugin", "index.js")
src = open(plugin, encoding="utf-8").read()

print("=" * 74)
print("E-257 判断哪些模块级状态真有风险")
print("=" * 74)

# ---------- ① 分析每个状态的生命周期语义 ----------
print("\n① 逐个分析")

analysis = [
    {
        "name": "sessions",
        "code": "const sessions = new Map()",
        "semantics": "会话状态 sid -> {prewarm, processed, injected}",
        "after_reload": "旧 sid 不再出现，残留条目无害（查不到就用不到）",
        "risk": "low",
        "action": "保留（可考虑加清理）",
    },
    {
        "name": "injectHistory",
        "code": "const injectHistory = []",
        "semantics": "注入历史（内存）",
        "after_reload": "历史保留，用于 UI 展示，无害",
        "risk": "low",
        "action": "保留",
    },
    {
        "name": "scanCursor",
        "code": "const scanCursor = new Map()",
        "semantics": "扫描游标 sid -> lastSeq",
        "after_reload": "旧 sid 的游标无用，新会话会重置",
        "risk": "low",
        "action": "保留",
    },
    {
        "name": "kernelPools",
        "code": "const kernelPools = new Map()",
        "semantics": "内核进程池 dbPath -> {proc, ...}",
        "after_reload": "旧进程还在跑，复用可省 7 秒启动",
        "risk": "medium",
        "action": "保留但需检查进程存活",
    },
    {
        "name": "REGISTERED_BY_CTX",
        "code": "const REGISTERED_BY_CTX = new WeakMap()",
        "semantics": "路由注册记录，按 ctx 隔离",
        "after_reload": "ctx 换 → 新 Set → 重新注册",
        "risk": "none",
        "action": "已修正，无风险",
    },
]

for a in analysis:
    icon = {"low": "🟢", "medium": "🟡", "none": "✅"}[a["risk"]]
    print(f"\n   {icon} {a['name']}")
    print(f"      语义: {a['semantics']}")
    print(f"      重载后: {a['after_reload']}")
    print(f"      处置: {a['action']}")

# ---------- ② 检查 kernelPools 是否有存活检查 ----------
print("\n② kernelPools 是否检查进程存活")

# 找 getKernel 的实现
idx = src.find("function getKernel")
if idx > 0:
    seg = src[idx:idx + 1200]
    has_alive_check = any(k in seg for k in
                          ["killed", "exitCode", "proc.killed", "stdin.destroyed"])
    print(f"   有存活检查: {'✅ 是' if has_alive_check else '❌ 否'}")
    if not has_alive_check:
        print("""
   ⚠ 潜在问题：
     如果内核进程已崩溃但 kernelPools 里还留着记录，
     复用时会往一个死进程写数据 → 请求超时或报错。

   需要确认实际情况：内核进程崩溃后能否被感知并重建。
""")
    # 展示相关代码
    print("\n   相关代码：")
    for line in seg.split("\n")[:30]:
        if any(k in line for k in ["kernelPools", "get(", "proc", "kill", "spawn"]):
            print(f"     {line.strip()[:80]}")
else:
    print("   ❌ 找不到 getKernel")

# ---------- ③ 结论 ----------
print("\n" + "=" * 74)
print("结论")
print("=" * 74)

medium = [a for a in analysis if a["risk"] == "medium"]
print(f"""
  5 处模块级状态中：
    · {len([a for a in analysis if a['risk']=="none"])} 处已修正（REGISTERED_BY_CTX）
    · {len([a for a in analysis if a['risk']=="low"])} 处低风险（会话类状态，残留无害）
    · {len(medium)} 处中风险（kernelPools，需检查进程存活）

  结论：E-254 那类"必须按实例隔离"的情况只有路由注册一处，
        已经修好。其余状态跨重载保持是**设计意图**，不应盲目改。
""")

out = {
    "experiment": "E-257",
    "analysis": analysis,
    "kernelPools_has_alive_check": has_alive_check if idx > 0 else None,
}
p = os.path.join(_HERE, "results", "e257_risk_analysis.json")
with open(p, "w", encoding="utf-8") as f:
    json.dump(out, f, ensure_ascii=False, indent=1)
print(f"写入 results/e257_risk_analysis.json")
