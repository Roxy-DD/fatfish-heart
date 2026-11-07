"""E-239 重启后验证：插件路由是否注册成功

你重启 DSH 后，我跑这个脚本确认：
  ① 所有 /cosmos-mem/* 路由是否可达
  ② 调试日志里是否有 http-route-ok
  ③ 各端点返回是否正常
"""
import sys
import os
import json
import time
import urllib.request
import urllib.error

_HERE = os.path.dirname(os.path.abspath(__file__))
LOG = os.path.join(_HERE, "cosmos_mem", "plugin-debug.jsonl")

# 可能的端口（从环境或参数取）
PORT = sys.argv[1] if len(sys.argv) > 1 else "54237"

ROUTES = [
    ("/cosmos-mem/stats", "GET"),
    ("/cosmos-mem/list?top=3", "GET"),
    ("/cosmos-mem/config", "GET"),
    ("/cosmos-mem/hook", "GET"),
    ("/cosmos-mem/seed", "GET"),
    ("/cosmos-mem/audit?limit=3", "GET"),
    ("/cosmos-mem/inject-history", "GET"),
    ("/cosmos-mem/sleep-history", "GET"),
]

print("=" * 72)
print(f"重启后路由验证（端口 {PORT}）")
print("=" * 72)

# ---------- ① 日志检查 ----------
print("\n① 调试日志：路由注册记录")
recent_ok = []
if os.path.exists(LOG):
    # 只看最近 500 行
    with open(LOG, "r", encoding="utf-8", errors="replace") as f:
        lines = f.readlines()[-500:]
    for line in lines:
        if "http-route-ok" in line:
            try:
                d = json.loads(line)
                recent_ok.append(d.get("path"))
            except Exception:
                pass
    # 去重
    recent_ok = sorted(set(recent_ok))

if recent_ok:
    print(f"   ✅ 找到 {len(recent_ok)} 条注册记录：")
    for p in recent_ok:
        print(f"      {p}")
else:
    print("   ❌ 未找到 http-route-ok 记录")
    print("      → 插件可能仍未加载，或 debugLog 未开启")

# ---------- ② 实际请求 ----------
print(f"\n② 实际请求测试")
reachable = 0
for path, method in ROUTES:
    url = f"http://127.0.0.1:{PORT}{path}"
    try:
        req = urllib.request.Request(url, method=method)
        with urllib.request.urlopen(req, timeout=10) as r:
            body = r.read(300).decode("utf-8", errors="replace")
            print(f"   ✅ {path}")
            print(f"      HTTP {r.status}  {body[:150]}")
            reachable += 1
    except urllib.error.HTTPError as e:
        print(f"   ❌ {path}  → HTTP {e.code}")
    except Exception as e:
        print(f"   ❌ {path}  → {type(e).__name__}: {str(e)[:60]}")

# ---------- ③ 汇总 ----------
print("\n" + "=" * 72)
print(f"可达: {reachable}/{len(ROUTES)}")
if reachable == len(ROUTES):
    print("✅ 全部路由正常，插件已正确加载")
elif reachable > 0:
    print("⚠ 部分路由可用，可能是旧代码（缺新路由）")
else:
    print("❌ 全部 404，插件未加载")
print("=" * 72)

sys.exit(0 if reachable == len(ROUTES) else 1)
