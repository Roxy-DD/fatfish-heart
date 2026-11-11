import sys, os, json, subprocess
sys.path.insert(0, r"proto")
sys.path.insert(0, r"proto\cosmos_mem")
import lifecycle as lc

m = lc._load_manifest()
registered = {v["id"] for v in m["versions"]}
tags = set(lc.all_tags())
missing = sorted(tags - registered)
print("registered:", len(registered))
print("git tags  :", len(tags))
print("missing   :", len(missing))

# ① 未登记版本能不能被 migrate_to 找到？
for probe in ["E-300", "E-431"]:
    found = lc._find(m, probe)
    print(f"  _find({probe}) -> {found}")

# ② 方向判定对不对？（E-300 在时间上远晚于 E-235-baseline）
for cur, tgt in [("E-235-baseline", "E-300"), ("E-235-baseline", "E-234")]:
    print(f"  _direction_of({cur} -> {tgt}) = {lc._direction_of(cur, tgt)}")

# ③ migrate_to 实际的失败形态（不执行 reset，只看是否在入口就抛）
for probe in ["E-300"]:
    try:
        r = lc.migrate_to(probe, "probe")
        print("  migrate_to ->", r["ok"])
    except lc.VersionError as e:
        print("  migrate_to raises VersionError:", str(e)[:90])
    except Exception as e:
        print("  migrate_to raises", type(e).__name__, str(e)[:90])

# ④ 未登记版本有没有 artifacts（版本级环的必要产物）
print("  snapshots dir has files for E-300?", os.path.exists(os.path.join(r"proto\work\snapshots", "E-300.db")))
