"""E-299 用 patch 策略测试连续重载"""
import json
import time
import urllib.request
import re
import os

PLUGIN = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "dsh-plugin", "index.js")
BASE = "http://127.0.0.1:61638/cosmos-mem"
PATTERN = re.compile(r"const RELOAD_MARKER = '([^']*)'")


def read_marker():
    try:
        with urllib.request.urlopen(BASE + "/selfreload", timeout=8) as r:
            return json.loads(r.read().decode("utf-8"))
    except Exception as e:
        return {"error": str(e)}


def set_marker(v):
    with open(PLUGIN, "r", encoding="utf-8") as f:
        s = f.read()
    m = PATTERN.search(s)
    if not m:
        return False
    s = s.replace(
        "const RELOAD_MARKER = '%s'" % m.group(1),
        "const RELOAD_MARKER = '%s'" % v)
    with open(PLUGIN, "w", encoding="utf-8") as f:
        f.write(s)
    return True


def reload_patch(strategy="patch"):
    req = urllib.request.Request(
        BASE + "/selfreload",
        data=json.dumps({"strategy": strategy}).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST")
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode("utf-8"))


print("=" * 70)
print("E-299 连续重载测试（patch 策略）")
print("=" * 70)

init = read_marker()
print("初始 marker:", init.get("__marker"))

ok_count = 0
results = []
for i in range(1, 4):
    v = "patch-%d-%d" % (i, int(time.time()))
    print("\n--- 第 %d 次 ---" % i)
    print("  设置: %s" % v)

    if not set_marker(v):
        print("  ❌ 设置 marker 失败")
        results.append({"cycle": i, "ok": False})
        continue

    try:
        r = reload_patch("patch")
        strategies = [t.get("strategy") for t in r.get("tried", []) if t.get("ok")]
        print("  reload ok=%s, 生效策略=%s" % (r.get("ok"), strategies))
    except Exception as e:
        print("  reload 请求失败: %s" % e)

    time.sleep(6)
    got = read_marker().get("__marker")
    print("  读回: %s" % got)
    ok = (got == v)
    if ok:
        print("  ✅ 成功")
        ok_count += 1
    else:
        print("  ❌ 失败")
    results.append({"cycle": i, "ok": ok, "expected": v, "got": got})

print("\n" + "=" * 70)
print("结果: %d/3 成功" % ok_count)
print("=" * 70)

out = {
    "experiment": "E-299",
    "strategy": "patch",
    "success": ok_count,
    "total": 3,
    "results": results,
}
p = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                 "results", "e299_patch_reload.json")
with open(p, "w", encoding="utf-8") as f:
    json.dump(out, f, ensure_ascii=False, indent=1)
print("写入 results/e299_patch_reload.json")
