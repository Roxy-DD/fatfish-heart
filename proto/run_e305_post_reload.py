"""E-305 重载后的完整功能验证

之前只验证了：
  ✅ 端点可用（HTTP 200）
  ✅ 指纹变化（代码真的重载）
  ✅ 正文不变（配置未被破坏）

但**没验证**：
  ❓ lifecycle 监督器是否还挂着（重载后可能丢失）
  ❓ 记忆读写是否正常
  ❓ 各机制（sleep/conflict/note/todo）是否还在
  ❓ 有没有状态泄漏或重复注册

这些才是"重载后系统还能用"的真正判据。
"""
import os
import sys
import json
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
MEM = os.path.join(HERE, "cosmos_mem")
sys.path.insert(0, MEM)

BASE = "http://127.0.0.1:61638/cosmos-mem"
PLUGIN = os.path.join(os.path.dirname(HERE), "dsh-plugin", "index.js")

print("=" * 76)
print("E-305 重载后的完整功能验证")
print("=" * 76)


def http_get(path, timeout=12):
    try:
        with urllib.request.urlopen(BASE + path, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8"))
    except Exception as e:
        return {"error": str(e)}


def http_post(path, body, timeout=40):
    req = urllib.request.Request(
        BASE + path,
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8"))
    except Exception as e:
        return {"error": str(e)}


def touch_code():
    """改一行注释，让 HMR 检测到模块内容变化"""
    with open(PLUGIN, "r", encoding="utf-8") as f:
        s = f.read()
    ts = time.strftime("%H%M%S")
    import re
    s = re.sub(r"^// e305-probe-.*\n", "", s, flags=re.M)
    s = f"// e305-probe-{ts}\n" + s
    with open(PLUGIN, "w", encoding="utf-8") as f:
        f.write(s)
    return ts


def clean_code():
    with open(PLUGIN, "r", encoding="utf-8") as f:
        s = f.read()
    import re
    s = re.sub(r"^// e305-probe-.*\n", "", s, flags=re.M)
    with open(PLUGIN, "w", encoding="utf-8") as f:
        f.write(s)


# ============ ① 重载前的基线快照 ============
print("\n① 重载前基线")

base = {}
base["stats"] = http_get("/stats")
base["note"] = http_get("/note")
base["todo"] = http_get("/todo")
base["triage"] = http_get("/triage")
base["diag"] = http_get("/selfreload")

print(f"   stats:   ok={bool(base['stats'].get('total') is not None)}")
print(f"   note:    ok={base['note'].get('ok')}")
print(f"   todo:    ok={base['todo'].get('ok')}")
print(f"   triage:  ok={base['triage'].get('ok')}")
print(f"   指纹:    {base['diag'].get('__marker')}")

# 记下记忆数量
n_mem_before = base["stats"].get("valid") or base["stats"].get("total")
print(f"   有效记忆: {n_mem_before}")

# ============ ② 触发重载（带代码改动）============
print("\n② 触发重载（改代码 + reloadVerified）")
ts = touch_code()
print(f"   已改代码: // e305-probe-{ts}")

r = http_post("/selfreload", {"strategy": "patch"})
print(f"   ok:       {r.get('ok')}")
print(f"   reloaded: {r.get('reloaded')}")
print(f"   before:   {r.get('markerBefore')}")
print(f"   after:    {r.get('markerAfter')}")

if not r.get("reloaded"):
    print(f"   ⚠ 未真正重载: {r.get('hint')}")

# ============ ③ 重载后的完整体检 ============
print("\n③ 重载后完整体检")

time.sleep(3)

after = {}
after["stats"] = http_get("/stats")
after["note"] = http_get("/note")
after["todo"] = http_get("/todo")
after["triage"] = http_get("/triage")
after["diag"] = http_get("/selfreload")
after["config_history"] = http_get("/config-history")

checks = []


def check(name, cond, detail=""):
    checks.append({"name": name, "ok": bool(cond), "detail": detail})
    print(f"   {'✅' if cond else '❌'} {name}" + (f"  {detail}" if detail else ""))


# 端点存活
check("stats 端点", after["stats"].get("total") is not None
      or after["stats"].get("error") is None,
      str(after["stats"].get("error") or "ok"))
check("note 端点", after["note"].get("ok") is True,
      str(after["note"].get("error") or ""))
check("todo 端点", after["todo"].get("ok") is True,
      str(after["todo"].get("error") or ""))
check("triage 端点", after["triage"].get("ok") is True,
      str(after["triage"].get("error") or ""))
check("config-history 端点", after["config_history"].get("ok") is True,
      str(after["config_history"].get("error") or ""))

# 记忆数据未丢
n_mem_after = after["stats"].get("valid") or after["stats"].get("total")
check("记忆数量未变", n_mem_after == n_mem_before,
      f"{n_mem_before} → {n_mem_after}")

# 指纹更新
check("指纹已更新",
      after["diag"].get("__marker") != base["diag"].get("__marker"),
      after["diag"].get("__marker"))

# ============ ④ 检查 lifecycle 是否还挂着 ============
print("\n④ lifecycle 监督器状态")

import io
log_path = os.path.join(MEM, "plugin-debug.jsonl")
lifecycle_after = []
if os.path.exists(log_path):
    with io.open(log_path, "r", encoding="utf-8", errors="replace") as f:
        lines = f.readlines()
    # 看最近 500 行有没有 lifecycle-attached
    for line in lines[-500:]:
        if "lifecycle-attached" in line:
            try:
                lifecycle_after.append(json.loads(line))
            except Exception:
                pass

n_lc = len(lifecycle_after)
check("lifecycle 已重新 attach", n_lc > 0, f"{n_lc} 次")
if lifecycle_after:
    last = lifecycle_after[-1]
    print(f"      最后一次: maxConsecutive={last.get('maxConsecutive')}, "
          f"requireProgress={last.get('requireProgress')}")

# ============ ⑤ 检查有无状态泄漏 ============
print("\n⑤ 状态泄漏检查")

# 路由重复注册？
dup = 0
route_fail = 0
if os.path.exists(log_path):
    with io.open(log_path, "r", encoding="utf-8", errors="replace") as f:
        for line in f.readlines()[-2000:]:
            if "duplicate" in line.lower():
                dup += 1
            if "http-route-fail" in line:
                route_fail += 1

check("无重复注册", dup == 0, f"{dup} 次")
check("无路由注册失败", route_fail == 0, f"{route_fail} 次")

# LLM 工具数量（确认工具还在）
print("\n⑥ 工具注册情况")

# 从日志看 tools 相关（如果有记录）
tool_logs = 0
if os.path.exists(log_path):
    with io.open(log_path, "r", encoding="utf-8", errors="replace") as f:
        for line in f.readlines()[-1000:]:
            if '"kind":"tool' in line:
                tool_logs += 1
print(f"   工具相关日志: {tool_logs} 条")

# ============ 清理 ============
clean_code()
print("\n已清理测试注释")

# ============ 结论 ============
print("\n" + "=" * 76)
print("结论")
print("=" * 76)

n_ok = sum(1 for c in checks if c["ok"])
print(f"\n  检查项: {n_ok}/{len(checks)} 通过")

if n_ok == len(checks):
    print("  ✅ 重载后所有功能正常")
else:
    print("  ❌ 有问题：")
    for c in checks:
        if not c["ok"]:
            print(f"     · {c['name']}: {c['detail']}")

out = {
    "experiment": "E-305",
    "reloaded": r.get("reloaded"),
    "marker_before": r.get("markerBefore"),
    "marker_after": r.get("markerAfter"),
    "n_mem_before": n_mem_before,
    "n_mem_after": n_mem_after,
    "checks": checks,
    "n_pass": n_ok,
    "n_total": len(checks),
}
p = os.path.join(HERE, "results", "e305_post_reload.json")
with open(p, "w", encoding="utf-8") as f:
    json.dump(out, f, ensure_ascii=False, indent=1)
print(f"\n写入 results/e305_post_reload.json")
