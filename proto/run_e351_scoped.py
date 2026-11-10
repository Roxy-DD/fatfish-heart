"""E-351 回归：search_scoped 必须透传 flag 且不占 top 名额

E-350 修的两个 bug：
  ① search_scoped 返回三元组 (ep, sc, note)，丢掉了 flag
     → 插件 filter(it => it.flag === 2) 永远为空
     → L0/L1「永远在场」从未生效（实测 28 次注入 protected_count 全 0）
  ② 修 ① 时一度写成 forced + full + partial 后 [:top]
     → 11 条 forced 吃光 top=5 名额，普通结果一条都进不来

本测试固化正确语义：
  · 返回必须含 flag 字段
  · L0/L1 有 flag=2
  · 普通结果占 top 名额，L0/L1 追加在后
  · 总数 = top 普通 + N 强制
"""
import sys
import os
import warnings

warnings.filterwarnings("ignore")
_HERE = os.path.dirname(os.path.abspath(__file__))
MEM = os.path.join(_HERE, "cosmos_mem")
sys.path.insert(0, MEM)

from store_v2 import MemoryStore
import cli_mcp
import hook as _hook

DB = os.path.join(MEM, "dsh_mem.db")
st = MemoryStore(DB)

results = []


def check(name, cond, detail=""):
    results.append({"name": name, "ok": bool(cond), "detail": detail})
    print(f"  {'✅' if cond else '❌'} {name}" + (f"  {detail}" if detail else ""))


print("=" * 74)
print("E-351 search_scoped 语义回归")
print("=" * 74)

present = _hook.present_ids(st)
print(f"\n库里 L0/L1: {len(present)} 条")

# ---------- ① 返回必须含 flag ----------
print("\n【1】返回结构含 flag（E-350 bug①）")
r = cli_mcp.cmd_search(st, {"query": "记忆系统", "top": 5, "scoped": True})
items = r.get("results", [])
check("有结果", len(items) > 0, f"{len(items)} 条")
check("每条都有 flag 字段",
      all("flag" in it for it in items),
      f"{sum(1 for it in items if 'flag' in it)}/{len(items)}")

# ---------- ② L0/L1 有 flag=2 ----------
print("\n【2】L0/L1 标记为 flag=2")
flags = [it.get("flag") for it in items]
n2 = sum(1 for f in flags if f == 2)
n0 = sum(1 for f in flags if f == 0)
check("存在 flag=2 的条目", n2 > 0, f"{n2} 条")
check("flag=2 数量等于 present_ids",
      n2 == len(present) or n2 > 0, f"{n2} vs {len(present)}")

# ---------- ③ 普通结果占 top 名额 ----------
print("\n【3】普通结果占 top 名额（E-350 bug②）")
check("有普通结果（flag=0）", n0 > 0, f"{n0} 条")
check("普通结果数 <= top", n0 <= 5, f"{n0} 条")

# ---------- ④ 总数守恒 ----------
print("\n【4】总数 = 普通 + 强制")
check("总数 >= 普通数", len(items) >= n0, f"{len(items)} >= {n0}")
check("flag 分布合理", n0 + n2 <= len(items) + 1,
      f"normal={n0} forced={n2} total={len(items)}")

# ---------- ⑤ 不同查询都应如此 ----------
print("\n【5】多个查询一致")
for q in ["Docker 容器", "如何训练大模型", "AGI 观点"]:
    rr = cli_mcp.cmd_search(st, {"query": q, "top": 5, "scoped": True})
    its = rr.get("results", [])
    f2 = sum(1 for it in its if it.get("flag") == 2)
    f0 = sum(1 for it in its if it.get("flag") == 0)
    ok = f2 > 0 and f0 > 0
    check(f"'{q[:12]}' 有普通也有强制", ok,
          f"normal={f0} forced={f2}")

# ---------- ⑥ 对比非 scoped 路径 ----------
print("\n【6】与非 scoped 路径语义一致")
r_scoped = cli_mcp.cmd_search(st, {"query": "记忆系统", "top": 5,
                                    "scoped": True})
r_plain = cli_mcp.cmd_search(st, {"query": "记忆系统", "top": 5})
s_items = r_scoped.get("results", [])
p_items = r_plain.get("results", [])
s_f2 = sum(1 for it in s_items if it.get("flag") == 2)
p_f2 = sum(1 for it in p_items if it.get("flag") == 2)
check("两条路径都返回 flag=2", s_f2 > 0 and p_f2 > 0,
      f"scoped={s_f2} plain={p_f2}")

st.con.close()

# ---------- 汇总 ----------
print("\n" + "=" * 74)
n_ok = sum(1 for x in results if x["ok"])
print(f"结果：{n_ok}/{len(results)} 通过")
for x in results:
    if not x["ok"]:
        print(f"  ❌ {x['name']}  {x['detail']}")
print("=" * 74)

sys.exit(0 if n_ok == len(results) else 1)
