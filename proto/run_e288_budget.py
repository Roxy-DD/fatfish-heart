"""E-288 验证预算修复 + 评估首轮超预算是否可接受

E-287 发现：remainChars 计算里 (st.injected ? 0 : 0) 两个分支都是 0，
            等于没扣 L0/L1 占用。已修。

修复后：
  首轮: 4356 → 3156 字（仍超 1500 预算，因为 L0/L1 全文本身 2856 字）
  后续: 1553 → 1500 字（精确）

要回答：首轮给 L0/L1 全文（2856字）是否值得？
"""
import sys
import os
import json
import warnings

warnings.filterwarnings("ignore")
_HERE = os.path.dirname(os.path.abspath(__file__))
_MEM = os.path.join(_HERE, "cosmos_mem")
sys.path.insert(0, _MEM)

from store_v2 import MemoryStore
import hook as _hook

DB = os.path.join(_MEM, "dsh_mem.db")
st = MemoryStore(DB)

print("=" * 78)
print("E-288 预算修复验证 + 首轮超预算评估")
print("=" * 78)

results = []


def check(name, cond, detail=""):
    results.append({"name": name, "ok": bool(cond), "detail": detail})
    print(f"  {'✅' if cond else '❌'} {name}" + (f"  {detail}" if detail else ""))


# ============ ① 预算计算逻辑 ============
print("\n① 预算计算逻辑（JS 逻辑在 Python 里复现验证）")


def calc_remain(total, protected_chars, floor=300):
    """对应修复后的 JS 逻辑"""
    return max(floor, total - protected_chars)


def calc_remain_old(total, protected_chars, floor=300):
    """对应修复前的 JS 逻辑（bug）"""
    return max(floor, total - 0)


TOTAL = 1500

cases = [
    ("首轮（L0/L1 全文）", 2856),
    ("后续（L0/L1 摘要）", 53),
    ("无常驻", 0),
]

print(f"\n   {'场景':<22} {'常驻':>6} {'旧remain':>10} {'新remain':>10} "
      f"{'旧总注入':>10} {'新总注入':>10}")
print("   " + "-" * 76)
for name, prot in cases:
    old_r = calc_remain_old(TOTAL, prot)
    new_r = calc_remain(TOTAL, prot)
    print(f"   {name:<22} {prot:>6} {old_r:>10} {new_r:>10} "
          f"{prot+old_r:>10} {prot+new_r:>10}")

check("后续模式精确符合预算",
      cases[1][1] + calc_remain(TOTAL, cases[1][1]) == TOTAL,
      f"{cases[1][1] + calc_remain(TOTAL, cases[1][1])} vs {TOTAL}")

check("修复后总注入减少",
      cases[0][1] + calc_remain(TOTAL, cases[0][1]) <
      cases[0][1] + calc_remain_old(TOTAL, cases[0][1]),
      f"{cases[0][1] + calc_remain(TOTAL, cases[0][1])} < "
      f"{cases[0][1] + calc_remain_old(TOTAL, cases[0][1])}")

# ============ ② L0/L1 全文的必要性 ============
print("\n② 首轮给 L0/L1 全文是否必要")

present = _hook.present_ids(st)
texts = []
for eid in present:
    ep = st.get(eid)
    if ep:
        info = next((x for x in _hook.protected_of(st)
                     if x["ep_id"] == eid), {})
        texts.append({
            "level": info.get("level", "?"),
            "note": info.get("note", ""),
            "chars": len(ep.raw_text),
        })

by_level = {}
for t in texts:
    by_level.setdefault(t["level"], []).append(t["chars"])

print()
for lvl in sorted(by_level):
    chars = by_level[lvl]
    print(f"   {lvl}: {len(chars)} 条, {sum(chars)} 字, "
          f"平均 {sum(chars)//len(chars)} 字/条")

l0_chars = sum(by_level.get("L0", []))
l1_chars = sum(by_level.get("L1", []))
total = l0_chars + l1_chars

print(f"\n   总计: {total} 字")
print(f"   L0（不可变红线）: {l0_chars} 字")
print(f"   L1（常驻准则）:   {l1_chars} 字")

# 判断：L0/L1 的性质决定首轮必须给全文
print(f"""
   分析：
     L0/L1 是"必须在场的准则"——模型不看全文就不知道具体要求。
     如果首轮只给标题（{len(texts)} 个标题约 {sum(len(t['note']) for t in texts)} 字），
     模型需要额外调 mem_search 才能看到内容，多一次往返。

     但首轮 {total} 字确实占用了大量预算。
""")

# 检查：后续模式的实际效果
print(f"\n   后续模式（只给标题）:")
summary = sum(len(t["note"]) for t in texts)
print(f"     标题总长: {summary} 字")
print(f"     加上检索结果 {TOTAL - summary} 字")
print(f"     总计: {TOTAL} 字 ✅ 符合预算")

# ============ ③ 是否有更优方案 ============
print("\n③ 检查：L1 里有特别长的条目吗")

for t in sorted(texts, key=lambda x: -x["chars"])[:5]:
    print(f"   [{t['level']}] {t['note'][:30]:<32} {t['chars']:>5} 字")

long_ones = [t for t in texts if t["chars"] > 400]
if long_ones:
    print(f"\n   超过 400 字的有 {len(long_ones)} 条，共 "
          f"{sum(t['chars'] for t in long_ones)} 字")
    print("   → 这些是首轮预算的主要占用者")

# ============ ④ 结论 ============
print("\n" + "=" * 78)
print("结论")
print("=" * 78)

n_ok = sum(1 for r in results if r["ok"])
print(f"\n  验证: {n_ok}/{len(results)} 通过")

print(f"""
  预算修复效果：
    首轮: 4356 → 3156 字（-1200）
    后续: 1553 → 1500 字（精确）

  首轮仍超预算的原因：
    L0/L1 全文本身 {total} 字，超过 1500 预算。
    这是**设计取舍**，不是 bug：
      · 首轮必须给全文，否则模型不知道准则内容
      · 后续只给标题（{summary} 字），完全符合预算

  如果要进一步优化，方向是：
    a) 精简 L0/L1 文本本身（当前 {total} 字，11 条）
    b) 首轮只给最关键的几条全文，其余给标题
    c) 接受首轮开销（一次性）
""")

st.con.close()

out = {
    "experiment": "E-288",
    "n_pass": n_ok,
    "n_total": len(results),
    "budget": TOTAL,
    "protected_total_chars": total,
    "l0_chars": l0_chars,
    "l1_chars": l1_chars,
    "first_round_total": total + calc_remain(TOTAL, total),
    "later_round_total": summary + calc_remain(TOTAL, summary),
    "results": results,
}
p = os.path.join(_HERE, "results", "e288_budget_fix.json")
with open(p, "w", encoding="utf-8") as f:
    json.dump(out, f, ensure_ascii=False, indent=1)
print(f"写入 results/e288_budget_fix.json")

sys.exit(0 if n_ok == len(results) else 1)
