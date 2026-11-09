"""E-298 验证自重启的稳定性：连续多次重载

E-297 单次验证成功（marker None → 新值）。
现在要验证：
  ① 连续 3 次重载都成功吗？
  ② 每次 marker 都更新吗？
  ③ 有没有状态泄漏（路由重复注册、内存增长）？
  ④ 重载后各机制还正常吗？
"""
import subprocess
import json
import time
import os
import re
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
PLUGIN = os.path.join(os.path.dirname(HERE), "dsh-plugin", "index.js")
PORT = 61638
BASE = f"http://127.0.0.1:{PORT}/cosmos-mem"

print("=" * 76)
print("E-298 自重启稳定性：连续多次重载")
print("=" * 76)


def read_marker():
    try:
        with urllib.request.urlopen(f"{BASE}/selfreload", timeout=8) as r:
            return json.loads(r.read().decode("utf-8"))
    except Exception as e:
        return {"error": str(e)}


def set_marker(new_val):
    """改代码里的 marker"""
    with open(PLUGIN, "r", encoding="utf-8") as f:
        src = f.read()
    m = re.search(r"const RELOAD_MARKER = '([^']*)'", src)
    if not m:
        return False
    src = src.replace(
        f"const RELOAD_MARKER = '{m.group(1)}'",
        f"const RELOAD_MARKER = '{new_val}'")
    with open(PLUGIN, "w", encoding="utf-8") as f:
        f.write(src)
    return True


def do_reload():
    try:
        req = urllib.request.Request(
            f"{BASE}/selfreload",
            data=json.dumps({"strategy": "configEditor"}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST")
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read().decode("utf-8"))
    except Exception as e:
        return {"ok": False, "error": str(e)}


# ============ 初始状态 ============
print("\n① 初始状态")
before = read_marker()
print(f"   marker: {before.get('__marker')}")
print(f"   entryFound: {before.get('entryFound')}")
print(f"   路由/服务: configEditor={before.get('hasConfigEditor')}")

results = []

# ============ 连续 3 次重载 ============
print("\n② 连续 3 次重载")

for i in range(1, 4):
    new_val = f"cycle-{i}-{int(time.time())}"
    print(f"\n   --- 第 {i} 次 ---")
    print(f"   设置 marker: {new_val}")

    if not set_marker(new_val):
        print("   ❌ 设置 marker 失败")
        results.append({"cycle": i, "ok": False, "reason": "set marker failed"})
        continue

    r = do_reload()
    print(f"   reload: ok={r.get('ok')}")
    time.sleep(4)

    after = read_marker()
    got = after.get("__marker")
    ok = got == new_val
    print(f"   读回 marker: {got}")
    print(f"   结果: {'✅ 成功' if ok else '❌ 失败'}")
    results.append({
        "cycle": i, "ok": ok,
        "expected": new_val, "got": got,
    })

# ============ 检查重载后的健康状态 ============
print("\n③ 重载后的健康检查")

health = read_marker()
print(f"   entryFound: {health.get('entryFound')}")
print(f"   entriesTotal: {health.get('entriesTotal')}")
print(f"   attempts: {health.get('attempts')}")

# 测试各端点是否还正常
print("\n   端点可用性:")
endpoints = [
    ("/stats", "统计"),
    ("/note", "笔记"),
    ("/todo", "待办"),
    ("/triage", "分诊"),
    ("/config-history", "配置历史"),
]
endpoint_ok = 0
for ep, name in endpoints:
    try:
        with urllib.request.urlopen(f"{BASE}{ep}", timeout=8) as r:
            if r.status == 200:
                endpoint_ok += 1
                print(f"     ✅ {name} ({ep})")
            else:
                print(f"     ❌ {name} HTTP {r.status}")
    except Exception as e:
        print(f"     ❌ {name}: {str(e)[:50]}")

print(f"\n   端点可用: {endpoint_ok}/{len(endpoints)}")

# ============ 检查日志有无异常 ============
print("\n④ 检查日志有无重复注册等异常")

log = os.path.join(HERE, "cosmos_mem", "plugin-debug.jsonl")
if os.path.exists(log):
    fails = 0
    dup = 0
    total = 0
    with open(log, "r", encoding="utf-8", errors="replace") as f:
        for line in f.readlines()[-3000:]:
            total += 1
            if "http-route-fail" in line:
                fails += 1
            if "duplicate" in line.lower():
                dup += 1
    print(f"   最近 {total} 行日志:")
    print(f"     路由注册失败: {fails}")
    print(f"     重复注册: {dup}")
    if fails > 0 or dup > 0:
        print("     ⚠ 有异常，需要检查")
else:
    print("   日志不存在")

# ============ 结论 ============
print("\n" + "=" * 76)
print("结论")
print("=" * 76)

success = sum(1 for r in results if r["ok"])
print(f"\n  连续重载: {success}/{len(results)} 成功")
print(f"  端点健康: {endpoint_ok}/{len(endpoints)}")
print(f"  attempts 累计: {health.get('attempts')}")

if success == len(results) and endpoint_ok == len(endpoints):
    verdict = "stable"
    print("\n  ✅ 自重启稳定可用")
elif success == len(results):
    verdict = "reload_ok_endpoint_issue"
    print("\n  🟡 重载成功但有端点异常")
else:
    verdict = "unstable"
    print(f"\n  ❌ 不稳定：{len(results) - success} 次失败")

out = {
    "experiment": "E-298",
    "verdict": verdict,
    "cycles": len(results),
    "success": success,
    "endpoints_ok": endpoint_ok,
    "endpoints_total": len(endpoints),
    "results": results,
}
p = os.path.join(HERE, "results", "e298_stability.json")
with open(p, "w", encoding="utf-8") as f:
    json.dump(out, f, ensure_ascii=False, indent=1)
print(f"\n写入 results/e298_stability.json")
