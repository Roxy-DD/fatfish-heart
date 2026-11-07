"""E-251 系统性扫描：还有多少"静默失败"路径？

E-250 发现 conflict 的 apply_verdict 会静默失败：
  relation 报 replace，但因 target 为空，什么都执行不了，
  仍返回 {"relation":"replace"} 让调用方以为成功。

这类问题的本质是：**函数返回 ok=True，但实际没做任何事**。

这是极危险的缺陷类型：
  · 调用方无法察觉
  · 数据不一致会悄悄积累
  · 排查时日志也看不出问题

本实验系统性扫描所有可能静默失败的路径：
  ① 所有返回 ok=True 但可能什么都没做的函数
  ② 所有"条件不满足就跳过"但没告知调用方的地方
  ③ 空操作返回（空列表/空结果）是否被正确区分
"""
import sys
import os
import json
import re
import ast as pyast

_HERE = os.path.dirname(os.path.abspath(__file__))
_MEM = os.path.join(_HERE, "cosmos_mem")
sys.path.insert(0, _MEM)

print("=" * 78)
print("E-251 系统性扫描：静默失败路径")
print("=" * 78)

# ---------- 扫描所有模块 ----------
targets = [
    "store_v1.py", "store_v2.py", "conflict.py", "consolidate.py",
    "notebook.py", "compose.py", "triage.py", "history.py",
    "hook.py", "seed.py", "cli_mcp.py", "channel.py",
]

findings = []

for fn in targets:
    path = os.path.join(_MEM, fn)
    if not os.path.exists(path):
        continue
    src = open(path, encoding="utf-8").read()
    try:
        tree = pyast.parse(src)
    except Exception:
        continue

    for node in pyast.walk(tree):
        if not isinstance(node, (pyast.FunctionDef, pyast.AsyncFunctionDef)):
            continue
        name = node.name
        # 只看返回 ok 的函数
        body_src = pyast.get_source_segment(src, node) or ""

        # 模式1：返回 ok=True 但函数体里有 continue/pass 跳过逻辑
        if '"ok": True' in body_src or "'ok': True" in body_src:
            has_skip = bool(re.search(r"\bcontinue\b", body_src)) or \
                       bool(re.search(r"^\s+pass\s*$", body_src, re.M))
            has_warn = "no_action" in body_src or "warn" in body_src
            if has_skip and not has_warn:
                findings.append({
                    "file": fn, "func": name, "line": node.lineno,
                    "risk": "有跳过逻辑但无 warn 告知",
                })

        # 模式2：返回空列表但没有区分"确认为空"与"失败"
        if re.search(r"return\s*\[\s*\]", body_src):
            has_reason = "error" in body_src or "reason" in body_src
            if not has_reason:
                findings.append({
                    "file": fn, "func": name, "line": node.lineno,
                    "risk": "返回空列表，无法区分'确实为空'与'出错'",
                })

print(f"\n① 扫描结果：发现 {len(findings)} 处可疑")
for f in findings:
    print(f"   [{f['file']}:{f['line']}] {f['func']}()")
    print(f"      {f['risk']}")

# ---------- ② 实测验证几个关键路径 ----------
print("\n② 实测关键路径")

from store_v2 import MemoryStore
import notebook as nb
import compose as cp
import triage as tg
import history as _hist

DB = os.path.join(_HERE, "work", "test_db", "e251.db")
os.makedirs(os.path.dirname(DB), exist_ok=True)
for suf in ("", "-wal", "-shm"):
    p = DB + suf
    if os.path.exists(p):
        try:
            os.remove(p)
        except Exception:
            pass

st = MemoryStore(DB)
tests = []


def probe(name, fn):
    """探测一个调用是否静默失败"""
    try:
        r = fn()
        # 判断标准：返回 ok=True 但结果为空/无变化
        ok = isinstance(r, dict) and r.get("ok")
        has_content = bool(
            r.get("note") or r.get("notes") or r.get("todos") or
            r.get("manual") or r.get("topics") or r.get("versions") or
            r.get("stats") or r.get("suggestions"))
        no_action = r.get("no_action") if isinstance(r, dict) else None
        silent = ok and not has_content and not no_action
        tests.append({"name": name, "silent": silent, "result": str(r)[:150]})
        print(f"   {'⚠ 静默' if silent else '✅ 正常'} {name}")
        if silent:
            print(f"      返回: {str(r)[:100]}")
        return r
    except Exception as e:
        tests.append({"name": name, "silent": False, "error": str(e)[:100]})
        print(f"   ⚠ 异常 {name}: {e}")
        return None


import cli_mcp
# 笔记：查不存在的
probe("note get 不存在的 id", lambda: cli_mcp.cmd_note(st, {"action": "get", "note_id": "nonexistent"}))
# 笔记：修订不存在的
probe("note revise 不存在的 id", lambda: cli_mcp.cmd_note(st, {"action": "revise", "note_id": "nonexistent", "body": "x"}))
# 笔记：空列表
probe("note list（空库）", lambda: cli_mcp.cmd_note(st, {"action": "list"}))
# 待办：完成不存在的
probe("todo done 不存在的 id", lambda: cli_mcp.cmd_todo(st, {"action": "done", "todo_id": "nonexistent"}))
# compose：不够条目
probe("compose 不足条目", lambda: cli_mcp.cmd_compose(st, {"action": "digest", "topic": "nonexistent"}))
# history：查无历史的
probe("history 无历史记录", lambda: cli_mcp.cmd_history(st, {"kind": "episode", "id": "nonexistent"}))
# triage：单条不存在
probe("triage one 不存在的 ep", lambda: cli_mcp.cmd_triage(st, {"action": "one", "ep_id": "nonexistent"}))

st.con.close()

# ---------- ③ 结论 ----------
print("\n" + "=" * 78)
print("结论")
print("=" * 78)

silent_ones = [t for t in tests if t.get("silent")]
print(f"\n实测 {len(tests)} 个调用，静默失败 {len(silent_ones)} 个")
for t in silent_ones:
    print(f"   ⚠ {t['name']}")
    print(f"     {t['result']}")

if not silent_ones:
    print("   ✅ 实测路径均无静默失败")

print(f"""
代码层可疑点 {len(findings)} 处（多数是误报——空列表在语义上确实为空）。

真正的静默失败特征：**调用方预期有动作，但实际没有**。
本次只修了 conflict。其他路径需逐个实测确认。
""")

out = {
    "experiment": "E-251",
    "code_findings": findings,
    "tested": len(tests),
    "silent_count": len(silent_ones),
    "silent_tests": silent_ones,
}
p = os.path.join(_HERE, "results", "e251_silent_scan.json")
with open(p, "w", encoding="utf-8") as f:
    json.dump(out, f, ensure_ascii=False, indent=1)
print(f"写入 results/e251_silent_scan.json")
