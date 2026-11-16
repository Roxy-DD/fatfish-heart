"""E-22b 强化集成测试：覆盖全部审计埋点

修正 E-22 的两个测试缺陷：
  1. sleep_prepare 只有 1 条记忆 → 0 候选对（测试用例太弱）
  2. cmd_judge 参数传错（要 ep_id+raw，不是 text）

本测试覆盖：remember / recall / sleep / conflict / chain / affect 六类埋点
"""
import sys
import os
import json

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, "cosmos_mem"))

from store_v2 import MemoryStore
import cli_mcp
import audit as _audit_mod

db = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "proto", "work", "e22b_integration.db")
for suf in ("", "-wal", "-shm"):
    p = db + suf
    if os.path.exists(p):
        os.remove(p)

st = MemoryStore(db)

results = []


def check(name, cond, detail=""):
    results.append({"name": name, "ok": bool(cond), "detail": detail})
    print(f"  {'✅' if cond else '❌'} {name}" + (f"  {detail}" if detail else ""))


print("=" * 74)
print("E-22b 强化集成测试（覆盖全部审计埋点）")
print("=" * 74)

# ============ 准备多条相似记忆，让 sleep 有候选对 ============
print("\n【准备】写入多条相似记忆")
seeds = [
    "登录密码是 8848",
    "我电脑的登录密码是 8848，别忘",
    "测试服务器密码改成 5678 了",
    "测试服务器密码是 5678",
    "项目A做完了，已交付",
]
ep_ids = []
for s in seeds:
    r = cli_mcp.cmd_add(st, {"text": s})
    if r.get("ok"):
        ep_ids.append(r["ep_id"])
print(f"  写入 {len(ep_ids)} 条记忆")

# ============ 1. remember 埋点 ============
print("\n【1】remember 埋点（写入）")
logs = _audit_mod.query(st, kind="remember")
check("remember 有日志", len(logs) >= len(ep_ids), f"{len(logs)} 条")

# ============ 2. recall 埋点 ============
print("\n【2】recall 埋点（检索）")
cli_mcp.cmd_search(st, {"query": "密码", "top": 5})
cli_mcp.cmd_search(st, {"query": "服务器", "top": 5, "rerank": False})
logs = _audit_mod.query(st, kind="recall")
check("recall 有日志", len(logs) >= 2, f"{len(logs)} 条")

# ============ 3. sleep 埋点（真实合并） ============
print("\n【3】sleep 埋点（睡眠整理）")
prep = cli_mcp.cmd_sleep_prepare(st, {"limit": 20, "recent_n": 20})
n_pairs = prep.get("n_pairs", 0)
check("sleep_prepare 找到候选对", n_pairs > 0, f"{n_pairs} 对")

if n_pairs > 0:
    top = prep["pairs"][0]
    print(f"    最相似对 cos={top['cos']:.3f}")
    print(f"      A: {top['a_text'][:34]}")
    print(f"      B: {top['b_text'][:34]}")
    actions = [{
        "op": "merge",
        "keep_id": top["a_id"],
        "drop_id": top["b_id"],
        "merged_text": top["a_text"] + "；" + top["b_text"],
    }]
    ap = cli_mcp.cmd_sleep_apply(st, {"actions": actions})
    check("sleep_apply 成功", ap.get("ok"), "")

    # 红线：drop 记忆必须仍存在（只失效不删）
    dropped = st.get(top["b_id"])
    check("R2 红线：drop 仍存在", dropped is not None)
    check("R2 红线：drop 已失效", dropped is not None and dropped.invalid_at is not None)
    check("R2 红线：drop 有 superseded_by",
          dropped is not None and bool(dropped.superseded_by))

logs = _audit_mod.query(st, kind="sleep")
check("sleep 有日志", len(logs) >= 1, f"{len(logs)} 条")

# ============ 4. conflict 埋点（用正确参数） ============
print("\n【4】conflict 埋点（矛盾判定）")
# 写一条与"登录密码 8848"冲突的新记忆
r = cli_mcp.cmd_add(st, {"text": "登录密码改成 9999 了", "judge": True})
new_ep = r.get("ep_id")
judged_ok = False
if new_ep and r.get("need_judge"):
    # 构造一个模型输出（模拟模型判定为 replace）
    raw = json.dumps({
        "target": "登录密码是 8848",
        "relation": "replace",
        "need_expand": False,
        "merged_text": None,
        "reason": "密码已变更",
        "confidence": 0.9,
    }, ensure_ascii=False)
    jr = cli_mcp.cmd_judge(st, {"ep_id": new_ep, "raw": raw, "top_k": 10})
    judged_ok = bool(jr.get("ok"))
    check("cmd_judge 成功", jr.get("ok"), f"relation={jr.get('relation')}")
else:
    check("cmd_judge 成功", False, f"need_judge={r.get('need_judge')}")

logs = _audit_mod.query(st, kind="conflict")
check("conflict 有日志", len(logs) >= 1, f"{len(logs)} 条")

# ============ 5. affect 埋点 ============
print("\n【5】affect 埋点（情绪判断）")
if ep_ids:
    raw_aff = json.dumps({"affect": 0.9, "valence": -1, "reason": "测试情绪"},
                         ensure_ascii=False)
    ar = cli_mcp.cmd_affect(st, {"ep_id": ep_ids[0], "raw": raw_aff})
    check("cmd_affect 成功", ar.get("ok"), f"affect={ar.get('affect')}")
    logs = _audit_mod.query(st, kind="affect")
    check("affect 有日志", len(logs) >= 1, f"{len(logs)} 条")

# ============ 6. chain 埋点 ============
print("\n【6】chain 埋点（链式展开）")
if ep_ids:
    # chain_apply 需要模型的 raw（含 chain 数组）
    raw_chain = json.dumps({"chain": [seeds[1]], "reason": "同属密码"},
                           ensure_ascii=False)
    cr = cli_mcp.cmd_chain_apply(st, {
        "seed_ep_id": ep_ids[0],
        "raw": raw_chain,
        "query": "密码",
    })
    check("cmd_chain_apply 成功", cr.get("ok"), f"chain_count={cr.get('chain_count')}")
    logs = _audit_mod.query(st, kind="chain")
    check("chain 有日志", len(logs) >= 1, f"{len(logs)} 条")

# ============ 7. audit 查询与统计 ============
print("\n【7】audit 查询与统计")
au = cli_mcp.cmd_audit(st, {"limit": 200})
check("audit 查询成功", au.get("ok"))
stats = au.get("stats") or {}
by_kind = stats.get("by_kind") or {}
print(f"    by_kind: {by_kind}")
check("统计包含多类事件", len(by_kind) >= 4, f"{len(by_kind)} 类")

# 按 kind 过滤
for k in ["remember", "recall", "sleep", "conflict", "affect", "chain"]:
    f = cli_mcp.cmd_audit(st, {"kind": k, "limit": 100})
    n = len(f.get("logs", []))
    check(f"按 {k} 过滤可用", n >= 1, f"{n} 条")

st.con.close()

# ============ 汇总 ============
print("\n" + "=" * 74)
n_ok = sum(1 for r in results if r["ok"])
print(f"结果：{n_ok}/{len(results)} 通过")
for r in results:
    if not r["ok"]:
        print(f"  ❌ {r['name']}  {r['detail']}")
print("=" * 74)

out = {"experiment": "E-22b", "n_pass": n_ok, "n_total": len(results),
       "results": results}
os.makedirs(os.path.join(_HERE, "results"), exist_ok=True)
with open(os.path.join(_HERE, "results", "e22b_integration.json"), "w",
          encoding="utf-8") as f:
    json.dump(out, f, ensure_ascii=False, indent=1)
print(f"\n写入 results/e22b_integration.json")
sys.exit(0 if n_ok == len(results) else 1)
