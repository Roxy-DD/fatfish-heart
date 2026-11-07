"""E-29 生命周期三项改进验证

① L3 过时判定：睡眠包带 l3_candidates，模型判断后可退役
② sleep 扫描窗口：改成"自上次睡眠以来新增"（增量 + 兜底）
③ 演化链追溯：A → B → C 完整链
"""
import sys
import os
import json
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, "cosmos_mem"))

from store_v2 import MemoryStore
import cli_mcp
import consolidate as _con
import hook as _hook

db = os.path.join(_HERE, "work", "e29_lifecycle.db")
for suf in ("", "-wal", "-shm"):
    p = db + suf
    if os.path.exists(p):
        os.remove(p)

st = MemoryStore(db)
results = []


def check(name, cond, detail=""):
    results.append({"name": name, "ok": bool(cond), "detail": detail})
    print(f"  {'✅' if cond else '❌'} {name}" + (f"  {detail}" if detail else ""))


print("=" * 76)
print("E-29 生命周期三项改进验证")
print("=" * 76)

# ============================================================
# ② 睡眠扫描窗口：增量
# ============================================================
print("\n【②】睡眠扫描窗口：自上次睡眠以来新增")
print("  准备：先写 3 条，睡一次；再写 2 条")

for i in range(3):
    cli_mcp.cmd_add(st, {"text": f"旧记忆 {i}：项目阶段说明"})
time.sleep(0.05)

# 第一次睡（游标从 0 开始，走兜底 recent 窗口）
p1 = cli_mcp.cmd_sleep_prepare(st, {"limit": 20, "recent_n": 50})
check("首次睡眠走兜底窗口", p1["window"] == "recent", f"window={p1['window']}")
check("首次 cursor=0", p1["cursor"] == 0.0, f"cursor={p1['cursor']}")

# 睡一次（推进游标）
cli_mcp.cmd_sleep_apply(st, {"actions": []})
c1 = _con.get_cursor(st)
check("睡眠后游标推进", c1 > 0, f"cursor={c1:.1f}")

# 再写 2 条新记忆
for i in range(2):
    cli_mcp.cmd_add(st, {"text": f"新记忆 {i}：刚发生的事"})

p2 = cli_mcp.cmd_sleep_prepare(st, {"limit": 20, "recent_n": 50})
check("第二次睡眠走增量窗口", p2["window"] == "fresh", f"window={p2['window']}")
check("增量只含 2 条新记忆", len(p2["fresh"]) == 2, f"{len(p2['fresh'])} 条")
check("增量不含旧记忆",
      all("新记忆" in f["text"] for f in p2["fresh"]),
      "只扫新增")

# ============================================================
# ① L3 过时判定
# ============================================================
print("\n【①】L3 候选：打包给模型判断是否过时")
p3 = cli_mcp.cmd_sleep_prepare(st, {"limit": 20, "recent_n": 50})
check("睡眠包包含 l3_candidates", isinstance(p3.get("l3_candidates"), list))
check("L3 候选有内容", len(p3["l3_candidates"]) > 0,
      f"{len(p3['l3_candidates'])} 条")
if p3["l3_candidates"]:
    c = p3["l3_candidates"][0]
    check("L3 候选含 age_h（年龄）", "age_h" in c, f"age_h={c.get('age_h')}")

# 模拟模型判断"旧记忆 0 已过时"
old_ids = [r["ep_id"] for r in p3["l3_candidates"]
           if "旧记忆 0" in r["text"]]
if old_ids:
    r = cli_mcp.cmd_sleep_apply(st, {
        "actions": [{"op": "invalidate", "ep_id": old_ids[0]}],
        "advance_cursor": False,
    })
    ok = len(r["result"]["invalidated"]) == 1
    check("模型判定过时后成功退役", ok, f"invalidated={r['result']['invalidated']}")

    # L0/L1/L2 应该被拒绝
    l0 = [p for p in _hook.protected_of(st) if p["level"] == "L0"]
    if l0:
        r2 = cli_mcp.cmd_sleep_apply(st, {
            "actions": [{"op": "invalidate", "ep_id": l0[0]["ep_id"]}],
            "advance_cursor": False,
        })
        check("L0 仍拒绝退役", len(r2["result"]["invalidated"]) == 0)

# ============================================================
# ③ 演化链追溯
# ============================================================
print("\n【③】演化链追溯 A → B → C")

# 造一条链：A 被 B 取代，B 被 C 取代
a = cli_mcp.cmd_add(st, {"text": "测试密码是 1111"})
b = st.supersede(a["ep_id"], "测试密码改成 2222")
c = st.supersede(b[1].ep_id, "测试密码改成 3333")

# 从 A 追溯
t1 = cli_mcp.cmd_trace(st, {"ep_id": a["ep_id"]})
check("从 A 追溯成功", t1.get("ok"), f"chain_len={t1.get('length')}")
check("链条长度 = 3", t1.get("length") == 3, f"{t1.get('length')}")
check("root 是 A", t1.get("root") == a["ep_id"])
check("tip 是最新", t1.get("tip") == c[1].ep_id, f"tip={t1.get('tip')[:8] if t1.get('tip') else None}")

if t1.get("chain"):
    texts = [x["text"] for x in t1["chain"]]
    print(f"     链条: {' → '.join(t[:16] for t in texts)}")
    check("链条顺序（老→新）", "1111" in texts[0] and "3333" in texts[-1])
    check("最新一条 is_current=True",
          t1["chain"][-1]["is_current"] is True)
    check("旧的 is_current=False",
          all(not x["is_current"] for x in t1["chain"][:-1]))

# 从中间追溯也应该拿到完整链
t2 = cli_mcp.cmd_trace(st, {"ep_id": b[1].ep_id})
check("从中间 B 追溯也完整", t2.get("length") == 3, f"{t2.get('length')}")

# 从末端追溯
t3 = cli_mcp.cmd_trace(st, {"ep_id": c[1].ep_id})
check("从末端 C 追溯也完整", t3.get("length") == 3, f"{t3.get('length')}")

# 不存在的 id
t4 = cli_mcp.cmd_trace(st, {"ep_id": "nonexistent123"})
check("不存在的 id 报错", t4.get("ok") is False)

st.con.close()

# ============================================================
print("\n" + "=" * 76)
n_ok = sum(1 for r in results if r["ok"])
print(f"结果：{n_ok}/{len(results)} 通过")
for r in results:
    if not r["ok"]:
        print(f"  ❌ {r['name']}  {r['detail']}")
print("=" * 76)

out = {"experiment": "E-29", "n_pass": n_ok, "n_total": len(results),
       "results": results}
os.makedirs(os.path.join(_HERE, "results"), exist_ok=True)
with open(os.path.join(_HERE, "results", "e29_lifecycle.json"), "w",
          encoding="utf-8") as f:
    json.dump(out, f, ensure_ascii=False, indent=1)
print(f"\n写入 results/e29_lifecycle.json")
sys.exit(0 if n_ok == len(results) else 1)
