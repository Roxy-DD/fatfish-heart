"""E-393 发现新问题：首轮注入 4803 字，超预算 3.2 倍

E-392 算出：
  protectedText ≈ 2856 字（11 条 L0/L1 全文）
  body          ≈ 1947 字（普通检索结果）
  content_chars ≈ 4803 字

但配置：autoInjectMaxChars = 1500

★ 矛盾：
  · E-348 修复让"首轮给全文"真正生效了
  · 但全文 2856 字 > 总预算 1500 字
  · 这意味着首轮必然超预算

查证：
  ① formatProtected 有没有受预算限制？
  ② remainChars 的计算是否真的扣除了 protectedChars？
  ③ 实际注入时 protectedText + body 会不会被截断？
"""
import sys
import os
import re
import warnings

warnings.filterwarnings("ignore")
_HERE = os.path.dirname(os.path.abspath(__file__))
IDX = os.path.join(os.path.dirname(_HERE), "dsh-plugin", "index.js")

print("=" * 76)
print("E-393 首轮注入是否超预算")
print("=" * 76)

src = open(IDX, encoding="utf-8").read()

# ---------- ① 预算计算逻辑 ----------
print("\n① remainChars 的计算")

i = src.find("const totalBudget")
if i > 0:
    seg = src[i:i + 900]
    print("   " + seg.replace("\n", "\n   ")[:850])

# ---------- ② formatProtected 是否有长度限制 ----------
print("\n② formatProtected 的实现")

i2 = src.find("function formatProtected")
if i2 > 0:
    seg2 = src[i2:i2 + 1400]
    print("   " + seg2.replace("\n", "\n   ")[:1300])

# ---------- ③ 检查 body 的预算 ----------
print("\n③ body 的长度限制")

m = re.search(r"formatMemories\(items,\s*([^,)]+)(?:,\s*([^)]+))?\)", src)
if m:
    print(f"   formatMemories 调用: items, {m.group(1)}"
          + (f", {m.group(2)}" if m.group(2) else ""))

# 找 remainChars 定义
m2 = re.search(r"const remainChars = ([\s\S]{0,300}?)\n\n", src)
if m2:
    print(f"\n   remainChars 定义:\n   {m2.group(1).strip()[:250]}")

# ---------- ④ 判定 ----------
print("\n④ 判定")

# 检查 protectedChars 是否从预算里扣除
has_deduct = "totalBudget - protectedChars" in src or \
             "protectedChars)" in src
print(f"   remainChars 是否扣除 protectedChars: "
      f"{'✅ 是' if has_deduct else '❌ 否'}")

# 检查 formatProtected 是否接受 maxChars 参数
fp_sig = re.search(r"function formatProtected\(([^)]*)\)", src)
if fp_sig:
    params = fp_sig.group(1)
    has_limit = "maxChars" in params or "limit" in params
    print(f"   formatProtected 参数: {params}")
    print(f"   是否有长度限制参数: {'✅ 是' if has_limit else '❌ 否'}")

print("""
   ★ 关键问题：

    如果 formatProtected **没有**长度限制参数，
    那么首轮会给全部 2856 字的 L0/L1 全文，
    不管预算多少。

    而 remainChars = max(300, totalBudget - protectedChars)
      = max(300, 1500 - 2856)
      = max(300, -1356)
      = 300

    → body 只剩 300 字
    → 总注入 = 2856 + 300 = 3156 字
    → 是预算 1500 的 2.1 倍

  这不是 bug（L0/L1 必须完整在场），
  但**预算语义被违反了**：
    autoInjectMaxChars 本意是"总预算"，实际只管 body。

  需要决定：
    A. 接受：L0/L1 优先，body 被压缩到 300
    B. 改语义：autoInjectMaxChars 只管 body，
       文档里说明 L0/L1 不占预算
    C. 首轮给摘要而非全文（推翻 E-348 的修复方向）
""")

# ---------- ⑤ 实测 ----------
print("\n⑤ 用真实数据实算")

import sys as _s
_s.path.insert(0, os.path.join(_HERE, "cosmos_mem"))
from store_v2 import MemoryStore
import cli_mcp
import hook as _hook

DB = os.path.join(_HERE, "cosmos_mem", "dsh_mem.db")
st = MemoryStore(DB)

r = cli_mcp.cmd_search(st, {"query": "测试", "top": 5, "scoped": True})
items = r.get("results", [])
protected = [it for it in items if it.get("flag") == 2]
normal = [it for it in items if it.get("flag") != 2]

p_chars = sum(len(it["text"]) for it in protected)
n_chars = sum(len(it["text"]) for it in normal)

BUDGET = 1500
remain = max(300, BUDGET - p_chars)

print(f"   L0/L1 全文:   {p_chars} 字")
print(f"   普通结果:     {n_chars} 字")
print(f"   总预算:       {BUDGET} 字")
print(f"   protectedChars: {p_chars}")
print(f"   remainChars:  max(300, {BUDGET} - {p_chars}) = {remain}")
print(f"\n   实际注入总量: {p_chars} + min({n_chars}, {remain})"
      f" = {p_chars + min(n_chars, remain)} 字")
print(f"   超出预算: {p_chars + min(n_chars, remain) - BUDGET} 字 "
      f"({(p_chars + min(n_chars, remain))/BUDGET:.1f}x)")

st.con.close()

out = {
    "experiment": "E-393",
    "protected_chars": p_chars,
    "normal_chars": n_chars,
    "budget": BUDGET,
    "remain_chars": remain,
    "actual_total": p_chars + min(n_chars, remain),
    "over_budget_ratio": round((p_chars + min(n_chars, remain)) / BUDGET, 2),
    "formatProtected_has_limit": False if fp_sig and "maxChars" not in fp_sig.group(1) else True,
}
import json
p = os.path.join(_HERE, "results", "e393_budget.json")
with open(p, "w", encoding="utf-8") as f:
    json.dump(out, f, ensure_ascii=False, indent=1)
print(f"\n写入 results/e393_budget.json")
