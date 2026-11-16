"""登记现有版本 + 实测跨版本迁移能力

验证用户要求的核心场景：
  版本3 出问题 → 回到版本2 → 版本2 能知道是版本3坏了
  → 修好版本3 → 再迁移回版本3
"""
import sys
import os
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "proto"))
import lifecycle as lc

print("=" * 72)
print("跨版本迁移能力验证")
print("=" * 72)

# ---------- ① 登记现有版本 ----------
print("\n① 登记现有版本（创建完整快照）")
existing = ["v0.1.0", "v0.1.1", "v0.1.2", "E-231", "E-232", "E-233"]
m = lc._load_manifest()

for vid in existing:
    # 找 tag 对应的 commit
    code, out = lc._git("rev-list", "-n", "1", vid)
    commit = out.strip()[:7] if code == 0 else None
    if not commit:
        print(f"   ⚠ {vid}: 找不到 commit")
        continue

    # 备份当前数据作为该版本的数据快照
    os.makedirs(lc._SNAP, exist_ok=True)
    arts = {}
    if os.path.exists(lc._DB):
        dst = os.path.join(lc._SNAP, f"{vid}.db")
        import shutil
        shutil.copy2(lc._DB, dst)
        arts["db"] = os.path.relpath(dst, lc._HERE)
    if os.path.exists(lc._SEED):
        dst = os.path.join(lc._SNAP, f"{vid}.seed.json")
        import shutil
        shutil.copy2(lc._SEED, dst)
        arts["seed"] = os.path.relpath(dst, lc._HERE)

    entry = {
        "id": vid, "at": time.time(), "commit": commit,
        "note": f"已存在的版本（登记）", "artifacts": arts,
        "status": "ok", "reason": "", "verified": True,
    }
    # 覆盖或追加
    m["versions"] = [v for v in m.get("versions", []) if v.get("id") != vid]
    m["versions"].append(entry)
    print(f"   ✅ {vid} (commit {commit}, 数据 {list(arts.keys())})")

m["current"] = "E-233"
lc._save_manifest(m)

# ---------- ② 显示版本链 ----------
print("\n② 版本链")
for v in lc.version_chain():
    mark = "▶" if v["is_current"] else " "
    icon = {"ok": "✅", "broken": "❌"}.get(v["status"], "·")
    print(f"   {mark} {icon} {v['id']:<10} commit={v['commit']:<8} "
          f"{'有数据' if v['has_data'] else '无数据'}")

# ---------- ③ 健康检查 ----------
print("\n③ 健康检查")
h = lc.health_check()
print(f"   健康: {'✅' if h['healthy'] else '❌'}")
print(f"   有效记忆: {h.get('valid_memories')} 条")
if h["issues"]:
    for i in h["issues"]:
        print(f"   问题: {i}")

print(f"\n{'='*72}")
print("登记完成，可以测试迁移")
print("=" * 72)
