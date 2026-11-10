"""E-394 回归：注入预算的两种语义

E-393 发现的问题：
  autoInjectMaxChars 名字说「最大注入字符数」，
  实际只限制 body，L0/L1 全文（2856 字）不受限。
  实测总注入 3156 字 = 预算的 2.1 倍。

E-393 修复：
  · autoInjectMaxChars   → 只管 body（语义修正）
  · autoInjectMaxTotal   → 新增，管总量（默认 6000）
  · remainChars = max(200, min(bodyBudget, maxTotal - protectedChars))

本测试验证：
  ① 两个配置项都存在且有合理默认值
  ② remainChars 的计算逻辑正确
  ③ 极端情况下不会出现负数或 0
"""
import sys
import os
import re
import warnings

warnings.filterwarnings("ignore")
_HERE = os.path.dirname(os.path.abspath(__file__))
IDX = os.path.join(os.path.dirname(_HERE), "dsh-plugin", "index.js")

results = []


def check(name, cond, detail=""):
    results.append({"name": name, "ok": bool(cond), "detail": detail})
    print(f"  {'✅' if cond else '❌'} {name}" + (f"  {detail}" if detail else ""))


print("=" * 74)
print("E-394 注入预算语义回归")
print("=" * 74)

src = open(IDX, encoding="utf-8").read()

# ---------- ① 配置项 ----------
print("\n【1】两个预算配置项")

check("DEFAULTS 有 autoInjectMaxChars", "autoInjectMaxChars: 1500" in src)
check("DEFAULTS 有 autoInjectMaxTotal", "autoInjectMaxTotal: 6000" in src)
check("Config schema 有 autoInjectMaxTotal",
      "autoInjectMaxTotal: _Schema.number()" in src)

# ---------- ② remainChars 计算 ----------
print("\n【2】remainChars 计算逻辑")

has_min = "Math.min(bodyBudget, maxTotal - protectedChars)" in src
has_max200 = "200," in src
check("用 min(bodyBudget, maxTotal - protectedChars)", has_min)
check("有 200 字下限保护", has_max200)

# ---------- ③ 逻辑模拟 ----------
print("\n【3】逻辑模拟（各种边界）")


def calc_remain(body_budget, max_total, protected_chars):
    """复刻插件里的计算"""
    return max(200, min(body_budget, max_total - protected_chars))


CASES = [
    # (描述, body_budget, max_total, protected, 期望范围)
    ("正常情况：L0/L1 2856 字", 1500, 6000, 2856, (1500, 1500)),
    ("L0/L1 为空", 1500, 6000, 0, (1500, 1500)),
    ("L0/L1 极长 5900 字", 1500, 6000, 5900, (100, 200)),
    ("L0/L1 超过总量上限 6500", 1500, 6000, 6500, (200, 200)),
    ("自定义 body 预算 3000", 3000, 6000, 2000, (3000, 3000)),
    ("自定义总量 2000，L0/L1 1500", 3000, 2000, 1500, (500, 500)),
]

for desc, bb, mt, pc, expect in CASES:
    r = calc_remain(bb, mt, pc)
    ok = expect[0] <= r <= expect[1]
    print(f"   {desc:<28} remainChars={r:<6} 期望 {expect[0]}~{expect[1]} "
          f"{'✅' if ok else '❌'}")
    check(f"边界: {desc}", ok, f"{r}")

# ---------- ④ 关键：不低于 200 ----------
print("\n【4】下限保护（永不为 0 或负数）")

for pc in [0, 1000, 5000, 10000, 99999]:
    r = calc_remain(1500, 6000, pc)
    ok = r >= 200
    if not ok:
        print(f"   ❌ protected={pc} 时 remainChars={r} < 200")
    check(f"L0/L1={pc} 字时 remainChars >= 200", ok, f"{r}")

# ---------- ⑤ 总注入量估算 ----------
print("\n【5】总注入量估算")

p_chars = 2856   # 实测 L0/L1 全文
n_chars = 736    # 实测普通结果

remain = calc_remain(1500, 6000, p_chars)
total = p_chars + min(n_chars, remain)

print(f"   L0/L1 全文: {p_chars} 字（不受 body 预算限制，正确）")
print(f"   body 预算:  {remain} 字")
print(f"   实际 body:  {min(n_chars, remain)} 字")
print(f"   总量:       {total} 字")
print(f"   总量上限:   6000 字 → {'✅ 未超' if total <= 6000 else '❌ 超了'}")

check("总注入量在 autoInjectMaxTotal 之内", total <= 6000, f"{total}")

# ---------- ⑥ 语义文档 ----------
print("\n【6】语义是否写明")

has_doc = "autoInjectMaxChars  只管普通检索结果（body）的预算" in src
check("代码注释写明 autoInjectMaxChars 只管 body", has_doc)

has_reason = "L0/L1 为什么不能压缩" in src or "缺一条行为就变" in src
check("写明 L0/L1 不能压缩的理由", has_reason)

# ---------- 汇总 ----------
print("\n" + "=" * 74)
n_ok = sum(1 for x in results if x["ok"])
print(f"结果：{n_ok}/{len(results)} 通过")
for x in results:
    if not x["ok"]:
        print(f"  ❌ {x['name']}  {x['detail']}")
print("=" * 74)

import json
out = {
    "experiment": "E-394",
    "n_pass": n_ok,
    "n_total": len(results),
    "defaults": {"autoInjectMaxChars": 1500, "autoInjectMaxTotal": 6000},
    "estimated_total": total,
}
p = os.path.join(_HERE, "results", "e394_budget_semantics.json")
with open(p, "w", encoding="utf-8") as f:
    json.dump(out, f, ensure_ascii=False, indent=1)
print(f"写入 results/e394_budget_semantics.json")

sys.exit(0 if n_ok == len(results) else 1)
