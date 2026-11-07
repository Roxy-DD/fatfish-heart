"""E-267b 用正确参数重测 chain 三个入口"""
import sys
import os

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, "cosmos_mem"))

from store_v2 import MemoryStore
import consolidate as _con
import cli_mcp

DB = os.path.join(_HERE, "cosmos_mem", "dsh_mem.db")
st = MemoryStore(DB)

print("=" * 74)
print("E-267b 用正确参数重测 chain")
print("=" * 74)

results = []


def check(name, cond, detail=""):
    results.append({"name": name, "ok": bool(cond), "detail": detail})
    print(f"  {'✅' if cond else '❌'} {name}" + (f"  {detail}" if detail else ""))


# ---------- ① evolution_chain（用正确的字段名 text）----------
print("\n【1】evolution_chain（字段名是 text 不是 raw_text）")
rows = st.con.execute(
    "SELECT ep_id FROM episodes WHERE superseded_by IS NOT NULL "
    "AND superseded_by != '' LIMIT 3").fetchall()

ok_chain = True
if rows:
    for (eid,) in rows[:2]:
        chain = _con.evolution_chain(st, eid)
        n = chain.get("length", 0)
        print(f"   {eid[:8]}: 长度 {n}, root={chain.get('root','')[:8]}, tip={chain.get('tip','')[:8]}")
        for item in chain.get("chain", []):
            txt = item.get("text", "")
            print(f"      · {item['ep_id'][:8]}: {txt[:48]}")
            if "text" not in item:
                ok_chain = False
    check("evolution_chain 正常返回", ok_chain)
    check("链长度 >= 2", any(_con.evolution_chain(st, e[0]).get("length", 0) >= 2
                             for e in rows[:2]))
else:
    check("有取代关系数据", False, "无数据")

# ---------- ② cmd_expand（用 prev_k / next_k）----------
print("\n【2】cmd_expand（参数是 prev_k / next_k）")
seed = st.con.execute(
    "SELECT ep_id, raw_text FROM episodes WHERE invalid_at IS NULL "
    "AND length(raw_text) > 80 LIMIT 1").fetchone()

if seed:
    sid = seed[0]
    r = cli_mcp.cmd_expand(st, {"ep_id": sid, "prev_k": 5, "next_k": 20})
    print(f"   ok={r.get('ok')}")
    cands = r.get("candidates") or []
    print(f"   新增候选: {len(cands)} 条")
    for c in cands[:4]:
        txt = c.get("text") or c.get("raw_text") or ""
        cos = c.get("cos", 0)
        print(f"      · (cos={cos:.2f}) {txt[:48]}")
    check("cmd_expand 返回候选", len(cands) > 0, f"{len(cands)} 条")
else:
    check("找到测试种子", False)

# ---------- ③ cmd_chain_prepare（用 seed_ep_id）----------
print("\n【3】cmd_chain_prepare（参数是 seed_ep_id）")
if seed:
    r = cli_mcp.cmd_chain_prepare(st, {"seed_ep_id": seed[0], "top_n": 20})
    print(f"   ok={r.get('ok')}")
    if r.get("ok"):
        cands = r.get("candidates") or []
        print(f"   候选: {len(cands)} 条")
        for c in cands[:4]:
            txt = c.get("text", "")
            print(f"      · (cos={c.get('cos',0):.2f}) {txt[:48]}")
        check("chain_prepare 返回候选", len(cands) > 0, f"{len(cands)} 条")
    else:
        check("chain_prepare 成功", False, str(r.get("error"))[:50])
        cands = []
else:
    check("找到测试种子", False)

# ---------- ④ 完整链路：prepare → 模拟模型判链 → apply ----------
print("\n【4】完整链路：prepare → 判链 → apply")
if seed:
    r = cli_mcp.cmd_chain_prepare(st, {"seed_ep_id": seed[0], "top_n": 10})
    cands = (r.get("candidates") or []) if r.get("ok") else []
    if cands:
        # 模拟模型判定：选第一条为同链
        raw = str({
            "is_chain": True,
            "target": cands[0].get("text", "")[:200],
            "reason": "测试",
            "confidence": 0.9,
        }).replace("'", '"')
        r2 = cli_mcp.cmd_chain_apply(st, {
            "seed_ep_id": seed[0],
            "raw": raw,
        })
        print(f"   chain_apply ok={r2.get('ok')}")
        print(f"   relation={r2.get('relation')}")
        print(f"   actions={r2.get('actions')}")
        check("chain_apply 可调用", r2.get("ok") is not None)
    else:
        check("有候选可判链", False)

st.con.close()

print("\n" + "=" * 74)
n_ok = sum(1 for r in results if r["ok"])
print(f"结果：{n_ok}/{len(results)} 通过")
for r in results:
    if not r["ok"]:
        print(f"  ❌ {r['name']}  {r['detail']}")
print("=" * 74)

import json
out = {"experiment": "E-267b", "n_pass": n_ok, "n_total": len(results),
       "results": results}
p = os.path.join(_HERE, "results", "e267b_chain.json")
with open(p, "w", encoding="utf-8") as f:
    json.dump(out, f, ensure_ascii=False, indent=1)
print(f"写入 results/e267b_chain.json")
sys.exit(0 if n_ok == len(results) else 1)
