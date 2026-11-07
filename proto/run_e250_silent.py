"""E-250 验证：conflict replace 的静默失败

E-249 发现：relation=replace 但旧记忆没失效。

假设根因：
  parse_model_output 期望字段是 "target"（原文文本），
  若模型给的是 target_ep_id 或 target=null，
  target_ep_id 为空 → apply_verdict 的 replace 分支条件不成立
  → 静默不执行任何动作，但 relation 仍报 replace

这是"静默失败"：调用方以为成功了，实际什么都没做。

本实验验证：
  ① 用正确字段名 target 是否能成功
  ② 用错误字段名 target_ep_id 是否静默失败
  ③ relation=replace 但 target=null 时的行为
"""
import sys
import os
import json

_HERE = os.path.dirname(os.path.abspath(__file__))
_MEM = os.path.join(_HERE, "cosmos_mem")
sys.path.insert(0, _MEM)

from store_v2 import MemoryStore
import cli_mcp
import conflict as _cf

DB = os.path.join(_HERE, "work", "test_db", "e250.db")
os.makedirs(os.path.dirname(DB), exist_ok=True)
for suf in ("", "-wal", "-shm"):
    p = DB + suf
    if os.path.exists(p):
        os.remove(p)

st = MemoryStore(DB)
results = []


def check(name, cond, detail=""):
    results.append({"name": name, "ok": bool(cond), "detail": detail})
    print(f"  {'✅' if cond else '❌'} {name}" + (f"  {detail}" if detail else ""))


print("=" * 76)
print("E-250 验证：conflict replace 的静默失败")
print("=" * 76)

OLD = "测试服务器密码是 8888"
NEW = "测试服务器密码改成了 9999"


_db_counter = [0]


def setup():
    """每次用独立 DB，避免文件占用"""
    _db_counter[0] += 1
    db = os.path.join(_HERE, "work", "test_db", f"e250_{_db_counter[0]}.db")
    os.makedirs(os.path.dirname(db), exist_ok=True)
    s = MemoryStore(db)
    r1 = cli_mcp.cmd_add(s, {"text": OLD})
    r2 = cli_mcp.cmd_add(s, {"text": NEW, "judge": True})
    return s, r1["ep_id"], r2["ep_id"], r2


# ---------- ① 正确字段名 target ----------
print("\n【1】正确字段名 target（原文匹配）")
st, old_id, new_id, prep = setup()
check("触发了判定", prep.get("need_judge"), f"need_judge={prep.get('need_judge')}")

if prep.get("need_judge"):
    raw_ok = json.dumps({
        "target": OLD,           # ← 正确字段：原文
        "relation": "replace",
        "reason": "密码已更新",
        "confidence": 0.95,
    }, ensure_ascii=False)
    r = cli_mcp.cmd_judge(st, {"ep_id": new_id, "raw": raw_ok, "top_k": 10})
    old_ep = st.get(old_id)
    check("judge 返回 ok", r.get("ok"))
    check("relation=replace", r.get("relation") == "replace")
    check("旧记忆已失效", old_ep.invalid_at is not None,
          f"invalid_at={old_ep.invalid_at}")
    check("旧记忆指向新的", old_ep.superseded_by == new_id,
          f"superseded_by={old_ep.superseded_by[:8] if old_ep.superseded_by else None}")
    check("actions 含 invalidated", "invalidated" in (r.get("actions") or {}),
          str(r.get("actions"))[:80])

st.con.close()

# ---------- ② 错误字段名 target_ep_id ----------
print("\n【2】错误字段名 target_ep_id（静默失败？）")
st, old_id, new_id, prep = setup()
if prep.get("need_judge"):
    raw_bad = json.dumps({
        "target_ep_id": old_id,   # ← 错误字段：解析器不认
        "relation": "replace",
        "reason": "密码已更新",
        "confidence": 0.95,
    }, ensure_ascii=False)
    r2 = cli_mcp.cmd_judge(st, {"ep_id": new_id, "raw": raw_bad, "top_k": 10})
    old_ep = st.get(old_id)
    print(f"   judge ok={r2.get('ok')}")
    print(f"   relation={r2.get('relation')}")
    print(f"   actions={r2.get('actions')}")
    print(f"   旧记忆 invalid_at={old_ep.invalid_at}")
    check("确认静默失败（relation 说 replace 但没执行）",
          r2.get("relation") == "replace" and old_ep.invalid_at is None,
          "这就是问题：调用方以为成功，实际没做事")

st.con.close()

# ---------- ③ target=null + relation=replace ----------
print("\n【3】relation=replace 但 target=null")
st, old_id, new_id, prep = setup()
if prep.get("need_judge"):
    raw_null = json.dumps({
        "target": None,
        "relation": "replace",
        "reason": "没找到目标",
        "confidence": 0.5,
    }, ensure_ascii=False)
    r3 = cli_mcp.cmd_judge(st, {"ep_id": new_id, "raw": raw_null, "top_k": 10})
    old_ep = st.get(old_id)
    print(f"   relation={r3.get('relation')}")
    print(f"   actions={r3.get('actions')}")
    check("target=null 时也确实没执行",
          old_ep.invalid_at is None, "同上：静默失败")

st.con.close()

# ---------- 结论 ----------
print("\n" + "=" * 76)
print("结论")
print("=" * 76)

n_ok = sum(1 for r in results if r["ok"])
print(f"\n验证项：{n_ok}/{len(results)} 通过")
for r in results:
    if not r["ok"]:
        print(f"  ❌ {r['name']}  {r['detail']}")

print("""
核心发现：
  ✅ 正确字段名 target 时，replace 完全正常（旧记忆失效+指向新记忆）
  ❌ 字段名错误 或 target=null 时，**静默失败**：
     relation 报 replace，但什么都没做，调用方无法察觉

  → 这是设计缺陷：apply_verdict 应该在这种情况下返回 warning，
    而不是静默返回 {"relation": "replace"} 让调用方以为成功。
""")

out = {"experiment": "E-250", "n_pass": n_ok, "n_total": len(results),
       "results": results}
p = os.path.join(_HERE, "results", "e250_silent_fail.json")
with open(p, "w", encoding="utf-8") as f:
    json.dump(out, f, ensure_ascii=False, indent=1)
print(f"写入 results/e250_silent_fail.json")
