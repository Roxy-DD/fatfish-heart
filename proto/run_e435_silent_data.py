"""E-435：验证"登记版本但无 artifacts"会不会静默丢数据

不碰真实仓库：monkeypatch lc._git 为 no-op，manifest/snapshots 指到临时目录。
"""
import sys, os, json, tempfile, shutil
sys.path.insert(0, r"proto")
sys.path.insert(0, r"proto\cosmos_mem")
import lifecycle as lc

tmp = tempfile.mkdtemp(prefix="e435_")
os.makedirs(os.path.join(tmp, "work"), exist_ok=True)
os.makedirs(os.path.join(tmp, "snap"), exist_ok=True)
lc._MANIFEST = os.path.join(tmp, "work", "versions.json")
lc._SNAP = os.path.join(tmp, "snap")

# 记录 _git 调用，但不真的执行
calls = []
def fake_git(*a, **k):
    calls.append(a)
    if a and a[0] == "rev-parse":
        return 0, "deadbee"
    return 0, ""
lc._git = fake_git

# 造两个版本：A 有 artifacts，B 没有 artifacts（模拟"只登记 tag"）
lc._save_manifest({
    "current": "A",
    "versions": [
        {"id": "A", "at": 1, "commit": "aaa111", "status": "ok", "reason": "",
         "note": "", "artifacts": {"db": "snap/A.db"}},
        {"id": "B", "at": 2, "commit": "bbb222", "status": "ok", "reason": "",
         "note": "", "artifacts": {}},
    ],
})

print("=" * 66)
print("E-435 无 artifacts 版本的迁移行为")
print("=" * 66)

r = lc.migrate_to("B", "测试")
print("\n迁移 A → B（B 无 artifacts）")
print(f"  ok       = {r['ok']}")
print(f"  direction= {r['direction']}")
print("\n步骤：")
for s in r["steps"]:
    print("  ·", s)

honest = any("跳过" in s or "无数据" in s or "缺少" in s or "artifacts" in s
             for s in r["steps"])
print(f"\n是否有任何一步提示'这个版本没有数据'？ {honest}")
print(f"结论：{'✅ 有提示' if honest else '❌ 静默通过 —— 代码迁了，数据没迁，且不报错'}")

# 再确认：artifacts 为空时循环根本不执行
print(f"\nmigrate_to 里数据循环是 for name, rel in (target.get('artifacts') or {{}}).items()")
print("→ 空 dict 时循环体一次都不执行，连 '跳过' 都不会打印")
shutil.rmtree(tmp, ignore_errors=True)
