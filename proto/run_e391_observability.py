"""E-391 回归：注入观测字段是否完整

E-390 发现的观测盲点：
  injectHistory 只记 protectedCount（条数），不记内容，
  导致两个修复无法验证：
    · E-350（L0/L1 是否真被注入）
    · E-348（首轮是否真给全文 vs 摘要）

E-390 已补：
  · 插件侧 injectRec 加 protectedMode / protectedPreview / isFirstTurn
  · 审计落库加 protected_mode / protected_preview / is_first_turn
  · cmd_inject_log 的 extra 白名单同步

本测试固化：这三个字段必须存在于整条链路。
"""
import sys
import os
import re
import warnings

warnings.filterwarnings("ignore")
_HERE = os.path.dirname(os.path.abspath(__file__))
MEM = os.path.join(_HERE, "cosmos_mem")
IDX = os.path.join(os.path.dirname(_HERE), "dsh-plugin", "index.js")
CLI = os.path.join(MEM, "cli_mcp.py")

results = []


def check(name, cond, detail=""):
    results.append({"name": name, "ok": bool(cond), "detail": detail})
    print(f"  {'✅' if cond else '❌'} {name}" + (f"  {detail}" if detail else ""))


print("=" * 74)
print("E-391 注入观测字段回归")
print("=" * 74)

idx = open(IDX, encoding="utf-8").read()
cli = open(CLI, encoding="utf-8").read()

# ---------- ① 插件侧 injectRec ----------
print("\n【1】injectRec 的观测字段")

check("injectRec 有 protectedMode", "protectedMode," in idx)
check("injectRec 有 protectedPreview", "protectedPreview:" in idx)
check("injectRec 有 isFirstTurn", "isFirstTurn," in idx
      or "isFirstTurn:" in idx)

# protectedMode 的计算逻辑必须正确
mode_ok = "const protectedMode = isFirstTurn ? 'full' : 'summary'" in idx
check("protectedMode 由 isFirstTurn 决定（语义正确）", mode_ok)

# ---------- ② 审计落库 ----------
print("\n【2】审计落库字段")

check("inject_log 传 protected_mode", "protected_mode: protectedMode" in idx)
check("inject_log 传 protected_preview",
      "protected_preview:" in idx)
check("inject_log 传 is_first_turn", "is_first_turn: isFirstTurn" in idx)

# ---------- ③ Python 侧白名单 ----------
print("\n【3】cmd_inject_log 的 extra 白名单")

check("extra 含 protected_mode", '"protected_mode"' in cli)
check("extra 含 protected_preview", '"protected_preview"' in cli)
check("extra 含 is_first_turn", '"is_first_turn"' in cli)

# ---------- ④ 端到端：字段能否真的落库 ----------
print("\n【4】端到端落库验证")

sys.path.insert(0, MEM)
try:
    import json
    import sqlite3
    from store_v2 import MemoryStore
    import cli_mcp

    DB = os.path.join(MEM, "dsh_mem.db")
    st = MemoryStore(DB)

    # 模拟一次带新字段的 inject_log
    test_sid = "__e391_test__"
    r = cli_mcp.cmd_inject_log(st, {
        "query": "E-391 测试注入",
        "mode": "query",
        "sid": test_sid,
        "ep_ids": ["testep1", "testep2"],
        "protected_count": 11,
        "content_chars": 1234,
        "has_prewarm": False,
        "protected_mode": "full",
        "protected_preview": "以下是系统长期准则，请在后续所有回答中遵循：...",
        "is_first_turn": True,
    })
    check("cmd_inject_log 返回 ok", r.get("ok") is True, str(r))

    # 从 DB 读回来
    row = st.con.execute(
        "SELECT extra FROM audit_log WHERE kind='inject' "
        "AND sid=? ORDER BY seq DESC LIMIT 1", (test_sid,)).fetchone()

    if row:
        d = json.loads(row[0] or "{}")
        check("落库后 protected_count 可读",
              d.get("protected_count") == 11, str(d.get("protected_count")))
        check("落库后 protected_mode 可读",
              d.get("protected_mode") == "full", str(d.get("protected_mode")))
        check("落库后 protected_preview 可读",
              bool(d.get("protected_preview")),
              str(d.get("protected_preview"))[:30])
        check("落库后 is_first_turn 可读",
              d.get("is_first_turn") is True,
              str(d.get("is_first_turn")))
    else:
        check("能读回落库记录", False, "未找到测试记录")

    # 清理测试记录
    st.con.execute("DELETE FROM audit_log WHERE sid=?", (test_sid,))
    st.con.commit()
    st.con.close()
    print("   （已清理测试记录）")

except Exception as e:
    check("端到端测试执行", False, f"{type(e).__name__}: {e}")

# ---------- 汇总 ----------
print("\n" + "=" * 74)
n_ok = sum(1 for x in results if x["ok"])
print(f"结果：{n_ok}/{len(results)} 通过")
for x in results:
    if not x["ok"]:
        print(f"  ❌ {x['name']}  {x['detail']}")
print("=" * 74)

out = {
    "experiment": "E-391",
    "n_pass": n_ok,
    "n_total": len(results),
    "fields": ["protected_mode", "protected_preview", "is_first_turn"],
}
p = os.path.join(_HERE, "results", "e391_observability.json")
with open(p, "w", encoding="utf-8") as f:
    json.dump(out, f, ensure_ascii=False, indent=1)
print(f"写入 results/e391_observability.json")

sys.exit(0 if n_ok == len(results) else 1)
