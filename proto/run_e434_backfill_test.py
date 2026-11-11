import importlib, sys
sys.path.insert(0, r"proto"); sys.path.insert(0, r"proto\cosmos_mem")
import lifecycle as lc
importlib.reload(lc)

print("="*66); print("E-434 补登记工具"); print("="*66)

# ① dry-run 不改文件
import hashlib
def h():
    p = lc._MANIFEST
    return hashlib.md5(open(p,"rb").read()).hexdigest() if __import__("os").path.exists(p) else "none"
before = h()
r = lc.backfill_tags(dry_run=True)
after = h()
print(f"\ndry-run: total_tags={r['total_tags']} registered={r['registered_before']} missing={len(r['missing'])}")
print(f"  manifest 未改动? {before == after}")
print(f"  missing 头 5 个: {r['missing'][:5]}")

# ② 真登记前先备份 manifest
import shutil, os, time
bak = lc._MANIFEST + ".bak_e434"
shutil.copy2(lc._MANIFEST, bak)

# ③ 只补登记 3 个做实测
r2 = lc.backfill_tags(limit=3, dry_run=False)
print(f"\n真登记 3 个: {r2['newly']}")
print(f"  登记后总数: {r2['registered_after']}")

# ④ 新登记版本能迁移吗（不真跑 git）
m = lc._load_manifest()
probe = r2["newly"][0]
found = lc._find(m, probe)
print(f"\n_find({probe}) -> {'OK commit=' + found['commit'] if found else 'None'}")
print(f"  at 来自 commit 时间（非 time.time）: {found['at'] if found else '-'}")
print(f"  _direction_of(E-235-baseline -> {probe}) = {lc._direction_of('E-235-baseline', probe)}")
print(f"  artifacts 为空（诚实标注无数据快照）: {found['artifacts'] if found else '-'}")

# ⑤ 重复登记应报错
try:
    lc.register_tag(probe)
    print("  ❌ 重复登记没有报错")
except lc.VersionError as e:
    print(f"  ✅ 重复登记被拒绝: {str(e)[:40]}")

# ⑥ 不存在的 tag 应报错
try:
    lc.register_tag("E-99999")
    print("  ❌ 不存在 tag 没报错")
except lc.VersionError as e:
    print(f"  ✅ 不存在 tag 被拒绝: {str(e)[:40]}")

# 还原
shutil.copy2(bak, lc._MANIFEST); os.remove(bak)
print(f"\n已还原 manifest: {h() == before}")
