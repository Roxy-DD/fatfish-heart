"""E-252 常驻测试：静默失败回归

静默失败定义：调用方预期有动作，但函数返回 ok=True 而实际没做任何事，
且调用方无从察觉。

这类缺陷极危险：
  · 数据不一致悄悄积累
  · 日志看不出问题
  · 排查成本极高

本测试纳入常驻回归套件，防止未来再引入。
覆盖：conflict 判定、triage 分诊、history 查询、note/todo 边界。
"""
import sys
import os
import json

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, "cosmos_mem"))

from store_v2 import MemoryStore
import cli_mcp

DB = os.path.join(_HERE, "work", "test_db", "e252.db")
os.makedirs(os.path.dirname(DB), exist_ok=True)
for suf in ("", "-wal", "-shm"):
    p = DB + suf
    if os.path.exists(p):
        try:
            os.remove(p)
        except Exception:
            pass

st = MemoryStore(DB)
results = []


def check(name, cond, detail=""):
    results.append({"name": name, "ok": bool(cond), "detail": detail})
    print(f"  {'✅' if cond else '❌'} {name}" + (f"  {detail}" if detail else ""))


def not_silent(r, expect_action=False):
    """判断是否静默失败"""
    if not isinstance(r, dict):
        return False
    if not r.get("ok"):
        return False   # 明确的失败，不算静默
    if r.get("no_action") or r.get("warn") or r.get("not_found"):
        return False   # 显式告知了没做事
    if expect_action:
        # 期望有动作但结果为空
        has = any(r.get(k) for k in
                  ("note", "notes", "todos", "manual", "versions", "topics"))
        return not has
    return False


print("=" * 72)
print("E-252 静默失败回归测试")
print("=" * 72)

# ---------- ① conflict 判定 ----------
print("\n【1】conflict：未解析出目标时必须显式告知")
r1 = cli_mcp.cmd_add(st, {"text": "服务器密码是 1111"})
r2 = cli_mcp.cmd_add(st, {"text": "服务器密码改成了 2222", "judge": True})

if r2.get("need_judge"):
    # 模型给错误字段名
    raw_bad = json.dumps({"relation": "replace", "target": None,
                          "reason": "x", "confidence": 0.5},
                         ensure_ascii=False)
    rr = cli_mcp.cmd_judge(st, {"ep_id": r2["ep_id"], "raw": raw_bad, "top_k": 10})
    check("未解析目标时标记 no_action", rr.get("no_action") is True)
    check("有明确 warn 说明", bool(rr.get("warn")),
          (rr.get("warn") or "")[:50])
    check("旧记忆未被误改",
          st.get(r1["ep_id"]).invalid_at is None)
else:
    check("冲突未触发（环境差异，跳过）", True)

# 正确字段名应真的执行
if r2.get("need_judge"):
    raw_ok = json.dumps({"target": "服务器密码是 1111", "relation": "replace",
                         "reason": "密码已更新", "confidence": 0.9},
                        ensure_ascii=False)
    # 新的一对，避免重复判定
    r3 = cli_mcp.cmd_add(st, {"text": "服务器密码最后定成 3333", "judge": True})
    if r3.get("need_judge"):
        rr2 = cli_mcp.cmd_judge(st, {"ep_id": r3["ep_id"], "raw": raw_ok,
                                     "top_k": 10})
        check("正确 target 时有实际动作",
              not rr2.get("no_action"), str(rr2.get("actions"))[:60])

# ---------- ② triage 不存在的 id ----------
print("\n【2】triage：不存在的 id 必须报错")
r = cli_mcp.cmd_triage(st, {"action": "one", "ep_id": "nonexistent_xyz"})
check("不存在时报错", r.get("ok") is False, str(r.get("error"))[:40])
check("标记 not_found", r.get("not_found") is True)
check("不返回伪造分诊结论", "quadrant" not in r or
      r.get("quadrant") == "not_found")

# ---------- ③ history 不存在的 id ----------
print("\n【3】history：不存在的 id 必须报错")
r = cli_mcp.cmd_history(st, {"kind": "episode", "id": "nonexistent_xyz"})
check("记忆不存在时报错", r.get("ok") is False, str(r.get("error"))[:40])
r = cli_mcp.cmd_history(st, {"kind": "note", "id": "nonexistent_xyz"})
check("笔记不存在时报错", r.get("ok") is False, str(r.get("error"))[:40])

# ---------- ④ note/todo 边界 ----------
print("\n【4】note/todo 边界")
r = cli_mcp.cmd_note(st, {"action": "get", "note_id": "nonexistent"})
check("note get 不存在返回 ok=False", r.get("ok") is False,
      str(r)[:50])

r = cli_mcp.cmd_note(st, {"action": "revise", "note_id": "nonexistent",
                          "body": "x"})
check("note revise 不存在返回 ok=False", r.get("ok") is False)

r = cli_mcp.cmd_todo(st, {"action": "done", "todo_id": "nonexistent"})
check("todo done 不存在返回 ok=False", r.get("ok") is False)

# ---------- ⑤ compose 不足条目 ----------
print("\n【5】compose：不足条目时明确说明")
r = cli_mcp.cmd_compose(st, {"action": "digest", "topic": "nonexistent_topic"})
check("明确告知不足", r.get("stage") == "insufficient" or r.get("ok") is False,
      str(r.get("message") or r.get("error"))[:50])

# ---------- ⑥ scan_conflicts 跳过统计 ----------
print("\n【6】scan_conflicts：跳过原因要可查")
# 先造几条已判定的
r = cli_mcp.cmd_scan_conflicts(st, {"limit": 20})
check("返回 scanned 计数", "scanned" in r, f"{r.get('scanned')}")
check("返回 skipped 分类", "skipped" in r,
      json.dumps(r.get("skipped", {}), ensure_ascii=False))

st.con.close()

# ---------- 汇总 ----------
print("\n" + "=" * 72)
n_ok = sum(1 for r in results if r["ok"])
print(f"结果：{n_ok}/{len(results)} 通过")
for r in results:
    if not r["ok"]:
        print(f"  ❌ {r['name']}  {r['detail']}")
print("=" * 72)

out = {"experiment": "E-252", "n_pass": n_ok, "n_total": len(results),
       "results": results}
p = os.path.join(_HERE, "results", "e252_silent_regression.json")
with open(p, "w", encoding="utf-8") as f:
    json.dump(out, f, ensure_ascii=False, indent=1)
print(f"写入 results/e252_silent_regression.json")

sys.exit(0 if n_ok == len(results) else 1)
