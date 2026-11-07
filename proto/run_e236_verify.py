"""E-236 验证：注入审计持久化

验证：
  ① inject_log 能写入
  ② inject_history 能读出
  ③ 跨进程重启后仍在（核心：持久化）
  ④ 数据完整（ep_ids / 字符数 / 模式）
"""
import sys
import os
import json
import subprocess

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, "cosmos_mem"))

from store_v2 import MemoryStore
import cli_mcp
import audit as _audit

DB = os.path.join(_HERE, "work", "e236_inject.db")
for suf in ("", "-wal", "-shm"):
    p = DB + suf
    if os.path.exists(p):
        os.remove(p)

st = MemoryStore(DB)
results = []


def check(name, cond, detail=""):
    results.append({"name": name, "ok": bool(cond), "detail": detail})
    print(f"  {'✅' if cond else '❌'} {name}" + (f"  {detail}" if detail else ""))


print("=" * 72)
print("E-236 验证：注入审计持久化")
print("=" * 72)

# 准备一些记忆
ids = []
for t in ["登录密码是8848", "测试服务器密码5678", "项目A已交付"]:
    r = cli_mcp.cmd_add(st, {"text": t})
    ids.append(r["ep_id"])

# ---------- ① 写入 ----------
print("\n【1】写入注入审计")
r1 = cli_mcp.cmd_inject_log(st, {
    "query": "帮我看下密码",
    "mode": "query",
    "sid": "sess_test_001",
    "ep_ids": ids[:2],
    "protected_count": 9,
    "content_chars": 1234,
    "has_prewarm": True,
})
check("写入成功", r1.get("ok"), f"logged={r1.get('logged')}")

# 再写一条
cli_mcp.cmd_inject_log(st, {
    "query": "项目进度如何",
    "mode": "slots",
    "sid": "sess_test_001",
    "ep_ids": [ids[2]],
    "protected_count": 9,
    "content_chars": 800,
    "has_prewarm": False,
})

# ---------- ② 读取 ----------
print("\n【2】读取注入历史")
r2 = cli_mcp.cmd_inject_history(st, {"limit": 10})
check("读取成功", r2.get("ok"))
check("有 2 条记录", r2.get("count") == 2, f"{r2.get('count')}")

hist = r2.get("history", [])
if hist:
    h = hist[0]
    check("含 query", bool(h.get("query")), h.get("query", "")[:20])
    check("含 ep_ids", len(h.get("ep_ids") or []) > 0, f"{len(h.get('ep_ids') or [])} 个")
    check("含 content_chars", h.get("content_chars", 0) > 0,
          f"{h.get('content_chars')}")
    check("含 protected_count", h.get("protected_count") == 9)

# ---------- ③ 按会话过滤 ----------
print("\n【3】按会话过滤")
r3 = cli_mcp.cmd_inject_history(st, {"sid": "sess_test_001", "limit": 10})
check("按 sid 过滤有效", r3.get("count") == 2, f"{r3.get('count')}")
r4 = cli_mcp.cmd_inject_history(st, {"sid": "nonexistent", "limit": 10})
check("不存在的 sid 返回 0", r4.get("count") == 0)

# ---------- ④ audit_log 里确实有 ----------
print("\n【4】audit_log 记录确认")
stats = _audit.stats(st)
check("audit 统计含 inject", stats["by_kind"].get("inject", 0) == 2,
      f"{stats['by_kind'].get('inject', 0)}")

st.con.close()

# ---------- ⑤ 跨进程持久化（核心）----------
print("\n【5】跨进程持久化验证（关闭后再打开）")
script = f'''
import sys
sys.path.insert(0, r"{os.path.join(_HERE, 'cosmos_mem')}")
from store_v2 import MemoryStore
import cli_mcp
st = MemoryStore(r"{DB}")
r = cli_mcp.cmd_inject_history(st, {{"limit": 10}})
print("COUNT=%d" % r.get("count", -1))
st.con.close()
'''
tmp = os.path.join(_HERE, "work", "_e236_reopen.py")
with open(tmp, "w", encoding="utf-8") as f:
    f.write(script)

env = dict(os.environ)
env["KMP_DUPLICATE_LIB_OK"] = "TRUE"
r = subprocess.run([sys.executable, tmp], capture_output=True, text=True,
                   timeout=120, env=env, encoding="utf-8", errors="replace")
out = (r.stdout or "") + (r.stderr or "")
ok_persist = "COUNT=2" in out
check("新进程仍能读到 2 条", ok_persist, out.strip()[:60])

os.remove(tmp)

# ---------- 汇总 ----------
print("\n" + "=" * 72)
n_ok = sum(1 for r in results if r["ok"])
print(f"结果：{n_ok}/{len(results)} 通过")
for r in results:
    if not r["ok"]:
        print(f"  ❌ {r['name']}  {r['detail']}")
print("=" * 72)

out_j = {"experiment": "E-236", "n_pass": n_ok, "n_total": len(results),
         "results": results}
p = os.path.join(_HERE, "results", "e236_inject_audit.json")
with open(p, "w", encoding="utf-8") as f:
    json.dump(out_j, f, ensure_ascii=False, indent=1)
print(f"\n写入 results/e236_inject_audit.json")

sys.exit(0 if n_ok == len(results) else 1)
