"""E-297 验证自重启是否真的重载代码

现在 /selfreload 端点已可用：
  entryFound: true, entry = {"id":"include:cosmos-mem"}

关键验证（之前没做的）：
  ① 在插件里加一个可观测的版本标记
  ② 调 /selfreload
  ③ 检查标记是否更新（= 代码真的重新执行了）

如果没有更新 → 说明只 reconcile 了配置，没重载模块
如果更新了 → 自重启真正可用
"""
import subprocess
import json
import time
import os
import re

HERE = os.path.dirname(os.path.abspath(__file__))
PLUGIN = os.path.join(os.path.dirname(HERE), "dsh-plugin", "index.js")
PORT = 61638
BASE = f"http://127.0.0.1:{PORT}/cosmos-mem"

print("=" * 76)
print("E-297 验证自重启是否重载代码")
print("=" * 76)

# ============ ① 读当前标记 ============
print("\n① 读当前代码里的标记")

with open(PLUGIN, "r", encoding="utf-8") as f:
    src = f.read()

m = re.search(r"const RELOAD_MARKER = '([^']*)'", src)
if m:
    old_marker = m.group(1)
    print(f"   已有标记: {old_marker}")
else:
    old_marker = None
    print("   尚无标记，需要添加")

# ============ ② 加/更新标记 ============
print("\n② 写入新标记")

new_marker = f"marker-{int(time.time())}"
if old_marker:
    src = src.replace(
        f"const RELOAD_MARKER = '{old_marker}'",
        f"const RELOAD_MARKER = '{new_marker}'")
    print(f"   替换: {old_marker} → {new_marker}")
else:
    # 在 apply 函数开头插入
    anchor = "export function apply(ctx, config) {"
    if anchor not in src:
        print("   ❌ 找不到 apply 函数")
        raise SystemExit(1)
    insert = (f"export function apply(ctx, config) {{\n"
              f"  // E-297 验证自重启用的标记：每次改这里，reload 后应能读到新值\n"
              f"  const RELOAD_MARKER = '{new_marker}'\n"
              f"  try {{ globalThis.__COSMOS_RELOAD_MARKER__ = RELOAD_MARKER }} catch {{}}\n")
    src = src.replace(anchor, insert, 1)
    print(f"   新增标记: {new_marker}")

with open(PLUGIN, "w", encoding="utf-8") as f:
    f.write(src)

# 语法检查
r = subprocess.run(["node", "-c", PLUGIN], capture_output=True,
                   text=True, timeout=60, encoding="utf-8", errors="replace")
print(f"   node -c: exit={r.returncode}")
if r.returncode != 0:
    print(f"   语法错误: {(r.stderr or '')[:300]}")
    raise SystemExit(1)

# ============ ③ 增加一个能读标记的端点 ============
print("\n③ 确认诊断端点能返回标记")

# 让 diagnose 带上 marker —— 先看 selfreload.js 是否暴露
sr_path = os.path.join(os.path.dirname(HERE), "dsh-plugin", "selfreload.js")
with open(sr_path, "r", encoding="utf-8") as f:
    sr_src = f.read()

if "__marker" not in sr_src:
    # 给 diagnose 加一行读 globalThis
    old_diag = "  function diagnose() {"
    new_diag = ("  function diagnose() {\n"
                "    // E-297：返回当前生效的代码标记（验证重载是否真的发生）\n"
                "    let marker = null\n"
                "    try { marker = globalThis.__COSMOS_RELOAD_MARKER__ || null } catch {}\n")
    if old_diag in sr_src:
        sr_src = sr_src.replace(old_diag, new_diag, 1)
        # 在返回对象里加 marker
        sr_src = sr_src.replace(
            "      entriesTotal: found.entries || 0,",
            "      entriesTotal: found.entries || 0,\n      __marker: marker,",
            1)
        with open(sr_path, "w", encoding="utf-8") as f:
            f.write(sr_src)
        print("   ✅ 已给 diagnose 加上 __marker")
    else:
        print("   ❌ 找不到 diagnose 函数")
else:
    print("   diagnose 已支持 __marker")

r2 = subprocess.run(["node", "-c", sr_path], capture_output=True,
                    text=True, timeout=60, encoding="utf-8", errors="replace")
print(f"   selfreload.js node -c: exit={r2.returncode}")

# ============ ④ 调用 reload ============
print("\n④ 调用 /selfreload 触发重载")

# 当前状态（重载前）
try:
    import urllib.request
    with urllib.request.urlopen(f"{BASE}/selfreload", timeout=10) as resp:
        before = json.loads(resp.read().decode("utf-8"))
    print(f"   重载前 marker: {before.get('__marker')}")
except Exception as e:
    print(f"   查询失败: {e}")
    before = {}

# 触发 reload
try:
    req = urllib.request.Request(
        f"{BASE}/selfreload",
        data=json.dumps({"strategy": "configEditor"}).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST")
    with urllib.request.urlopen(req, timeout=30) as resp:
        result = json.loads(resp.read().decode("utf-8"))
    print(f"   reload 结果: ok={result.get('ok')}")
    print(f"   尝试过的策略: {result.get('tried')}")
except Exception as e:
    print(f"   reload 请求失败: {e}")
    result = {}

# 等服务重启
time.sleep(5)

# ============ ⑤ 验证标记是否更新 ============
print("\n⑤ 验证标记是否更新")

try:
    with urllib.request.urlopen(f"{BASE}/selfreload", timeout=10) as resp:
        after = json.loads(resp.read().decode("utf-8"))
    new_read = after.get("__marker")
    print(f"   重载后 marker: {new_read}")
    print(f"   期望的 marker: {new_marker}")

    if new_read == new_marker:
        print("\n   ✅ 自重启成功！代码真的重新执行了")
        verdict = "success"
    elif new_read == before.get("__marker"):
        print("\n   ❌ 标记未变 —— 只 reconcile 了配置，没重载模块")
        verdict = "no_reload"
    elif new_read is None:
        print("\n   ⚠ 读不到 marker（可能是旧代码路径，或端点未更新）")
        verdict = "unknown"
    else:
        print(f"\n   🟡 标记变了但值不对: {new_read}")
        verdict = "mismatch"
except Exception as e:
    print(f"   查询失败: {e}")
    verdict = "error"

print("\n" + "=" * 76)
print("结论")
print("=" * 76)
print(f"\n  verdict: {verdict}")
print(f"  旧 marker: {before.get('__marker')}")
print(f"  新 marker: {new_marker}")

out = {
    "experiment": "E-297",
    "verdict": verdict,
    "old_marker": before.get("__marker"),
    "new_marker": new_marker,
    "reload_result": result,
}
p = os.path.join(HERE, "results", "e297_selfreload_verify.json")
with open(p, "w", encoding="utf-8") as f:
    json.dump(out, f, ensure_ascii=False, indent=1)
print(f"\n写入 results/e297_selfreload_verify.json")
