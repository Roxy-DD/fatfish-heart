"""E-302 改进后的自重启验证（干净版）

改进点：
  ① 带时间戳的备份（不覆盖历史）
  ② 正文逐字节不变（只动末尾标记行）
  ③ 写完读回校验，失败自动回滚
  ④ 备份最多保留 5 份

验证方法（不改插件源码）：
  · 每次 reload 前记录 globalThis 指纹（上一轮 apply 生成）
  · reload 后读新指纹，指纹变了 = 代码真的重新执行了
  · 同时检查正文、备份数量

注意：本测试会真的触发重载（这是被测行为），
      但不会改插件源码，也不会破坏用户配置（有备份 + 正文校验）。
"""
import os
import json
import time
import hashlib
import urllib.request
import glob

HERE = os.path.dirname(os.path.abspath(__file__))
PROF = os.path.expanduser("~/.dsh/profiles/desktop")
PATCH = os.path.join(PROF, "cordis.patch.yml")
BASE = "http://127.0.0.1:61638/cosmos-mem"


def strip_stamps(s):
    return "\n".join(
        line for line in s.split("\n")
        if not line.startswith("# __reload_stamp__")
    ).rstrip()


def read_patch():
    with open(PATCH, "r", encoding="utf-8") as f:
        return f.read()


def list_backups():
    return sorted(glob.glob(PATCH + ".cosmos-bak-*"))


def read_diag():
    try:
        with urllib.request.urlopen(BASE + "/selfreload", timeout=10) as r:
            return json.loads(r.read().decode("utf-8"))
    except Exception as e:
        return {"error": str(e)}


def reload_patch():
    req = urllib.request.Request(
        BASE + "/selfreload",
        data=json.dumps({"strategy": "patch"}).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST")
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode("utf-8"))


print("=" * 74)
print("E-302 改进后的自重启验证")
print("=" * 74)

# ---------- 基线 ----------
print("\n① 基线")
baseline = read_patch()
base_body = strip_stamps(baseline)
print(f"   patch: {len(baseline)} 字符")
print(f"   已有备份: {len(list_backups())} 份")

d0 = read_diag()
marker0 = d0.get("__marker")
print(f"   当前指纹: {marker0}")

if not marker0:
    print("\n   ⚠ 读不到指纹，说明插件还是旧版本（没有 apply 指纹）")
    print("   需要重启一次让新代码生效。")
    marker0 = None

# ---------- 连续 reload ----------
print("\n② 连续 3 次 reload")

results = []
prev_marker = marker0

for i in range(1, 4):
    print(f"\n   --- 第 {i} 次 ---")
    try:
        r = reload_patch()
        ok = r.get("ok")
        print(f"   reload ok={ok}")
        print(f"   bodyUnchanged={r.get('bodyUnchanged')}")
        bak = r.get("backup")
        print(f"   backup={os.path.basename(bak) if bak else '(无)'}")
    except Exception as e:
        print(f"   reload 失败: {e}")
        results.append({"cycle": i, "ok": False, "error": str(e)})
        continue

    time.sleep(6)

    d = read_diag()
    new_marker = d.get("__marker")
    changed = (new_marker != prev_marker) and bool(new_marker)
    print(f"   指纹: {prev_marker} → {new_marker}")
    print(f"   指纹变化: {'✅' if changed else '❌'}")

    cur = read_patch()
    body_ok = (strip_stamps(cur) == base_body)
    print(f"   正文不变: {'✅' if body_ok else '❌'}")

    n_bak = len(list_backups())
    print(f"   备份数: {n_bak}")

    results.append({
        "cycle": i, "ok": ok, "marker_changed": changed,
        "body_ok": body_ok, "n_backups": n_bak,
        "marker": new_marker,
    })
    prev_marker = new_marker

# ---------- 分析 ----------
print("\n" + "=" * 74)
print("③ 分析")
print("=" * 74)

n = len(results)
if n == 0:
    print("\n   ❌ 没有任何测试结果（全部失败）")
    verdict = "no_results"
else:
    changed_n = sum(1 for r in results if r.get("marker_changed"))
    body_n = sum(1 for r in results if r.get("body_ok"))
    ok_n = sum(1 for r in results if r.get("ok"))
    final_bak = len(list_backups())

    print(f"\n   reload 成功:   {ok_n}/{n}")
    print(f"   指纹变化:     {changed_n}/{n}  ← 这才是「代码真的重载」的证据")
    print(f"   正文不变:     {body_n}/{n}")
    print(f"   备份文件数:   {final_bak}（上限 5）")

    issues = []
    if ok_n < n:
        issues.append(f"reload 失败 {n - ok_n} 次")
    if changed_n < n:
        issues.append(f"指纹未变 {n - changed_n} 次（代码没真重载）")
    if body_n < n:
        issues.append(f"正文漂移 {n - body_n} 次")
    if final_bak > 5:
        issues.append(f"备份超限 {final_bak}")

    if issues:
        verdict = "issues"
        print("\n   ❌ 发现问题：")
        for x in issues:
            print(f"      · {x}")
    else:
        verdict = "pass"
        print("\n   ✅ 全部通过")
        print("      · 每次 reload 都成功")
        print("      · 每次指纹都变化（代码真的重新执行了）")
        print("      · 每次正文逐字节不变")
        print("      · 备份保留历史且数量受控")

print("\n   备份历史:")
for b in list_backups():
    print(f"     {os.path.basename(b)} ({os.path.getsize(b)} 字节)")

out = {
    "experiment": "E-302",
    "verdict": verdict,
    "results": results,
}
p = os.path.join(HERE, "results", "e302_improved_reload.json")
with open(p, "w", encoding="utf-8") as f:
    json.dump(out, f, ensure_ascii=False, indent=1)
print(f"\n写入 results/e302_improved_reload.json")
