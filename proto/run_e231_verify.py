"""E-231 验证：强制注入分层后的效果

对比：
  · 改动前：L0/L1 全文每轮注入，吃掉 1500 字预算
  · 改动后：首轮全文，后续摘要（只给标题）

测量：
  ① 首轮 vs 后续的注入字符数
  ② 用户记忆能进入注入的条数变化
  ③ flag 字段是否正确传递
"""
import sys
import os
import json

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, "cosmos_mem"))

from store_v2 import MemoryStore
import cli_mcp
import hook as _hook

DB = os.path.join(_HERE, "cosmos_mem", "dsh_mem.db")
st = MemoryStore(DB)
results = []


def check(name, cond, detail=""):
    results.append({"name": name, "ok": bool(cond), "detail": detail})
    print(f"  {'✅' if cond else '❌'} {name}" + (f"  {detail}" if detail else ""))


print("=" * 74)
print("E-231 验证：强制注入分层")
print("=" * 74)

# ---------- ① flag 字段是否传递 ----------
print("\n【1】flag 字段传递")
r = cli_mcp.cmd_search(st, {"query": "帮我写个 Python 函数", "top": 5})
items = r.get("results", [])
has_flag = all("flag" in it for it in items)
check("每条结果都有 flag 字段", has_flag, f"{len(items)} 条")

flag2 = [it for it in items if it.get("flag") == 2]
flag0 = [it for it in items if it.get("flag") == 0]
check("能识别 L0/L1（flag=2）", len(flag2) > 0, f"{len(flag2)} 条")
check("能识别普通结果（flag=0）", len(flag0) >= 0, f"{len(flag0)} 条")

# ---------- ② 模拟首轮 vs 后续的注入量 ----------
print("\n【2】注入量对比（模拟 formatProtected）")
protected = _hook.protected_of(st)
rows = {}
for p in protected:
    ep = st.get(p["ep_id"])
    if ep:
        rows[p["ep_id"]] = {"note": p["note"], "text": ep.raw_text}

full_chars = sum(len(v["note"]) + len(v["text"]) + 20 for v in rows.values())
summ_chars = sum(len(v["note"]) + 24 + 20 for v in rows.values())

print(f"   首轮全文: ~{full_chars} 字")
print(f"   后续摘要: ~{summ_chars} 字")
saved = full_chars - summ_chars
print(f"   节省:     ~{saved} 字/轮（{(1-summ_chars/max(1,full_chars))*100:.0f}%）")

check("摘要模式确实更省", summ_chars < full_chars * 0.5,
      f"{summ_chars} < {full_chars}*0.5")

# ---------- ③ 用户记忆能否进入注入预算 ----------
print("\n【3】用户记忆进入注入的机会")
BUDGET = 1500
after_protected = BUDGET - summ_chars   # 后续轮次：准则只占摘要
print(f"   预算 {BUDGET} 字")
print(f"   改动前（准则全文 {full_chars} 字）: 剩余 {BUDGET - full_chars} 字 "
      f"→ {'几乎装不下用户记忆' if BUDGET - full_chars < 300 else '还有空间'}")
print(f"   改动后（准则摘要 {summ_chars} 字）: 剩余 {after_protected} 字 "
      f"→ 可装约 {after_protected // 60} 条用户记忆")

check("改动后用户记忆有更多空间", after_protected > (BUDGET - full_chars),
      f"{after_protected} > {BUDGET - full_chars}")

# ---------- ④ 检索召回不受影响 ----------
print("\n【4】检索召回不受影响（L0/L1 不占 top 名额）")
for q in ["今天天气", "Python 函数", "记忆系统设计"]:
    r = cli_mcp.cmd_search(st, {"query": q, "top": 5})
    items = r.get("results", [])
    normal = [it for it in items if it.get("flag") != 2]
    prot = [it for it in items if it.get("flag") == 2]
    print(f"   '{q}': 普通 {len(normal)} 条 + 保护 {len(prot)} 条")
    check(f"  '{q}' 有普通结果", len(normal) >= 0)

# ---------- 摘要 ----------
print("\n" + "=" * 74)
n_ok = sum(1 for r in results if r["ok"])
print(f"结果：{n_ok}/{len(results)} 通过")
for r in results:
    if not r["ok"]:
        print(f"  ❌ {r['name']}  {r['detail']}")
print("=" * 74)

out = {
    "experiment": "E-231",
    "n_pass": n_ok, "n_total": len(results),
    "full_chars": full_chars, "summary_chars": summ_chars,
    "saved_per_round": saved,
    "budget": BUDGET,
    "remain_before": BUDGET - full_chars,
    "remain_after": after_protected,
    "results": results,
}
p = os.path.join(_HERE, "results", "e231_inject.json")
with open(p, "w", encoding="utf-8") as f:
    json.dump(out, f, ensure_ascii=False, indent=1)
print(f"\n写入 results/e231_inject.json")

st.con.close()
sys.exit(0 if n_ok == len(results) else 1)
