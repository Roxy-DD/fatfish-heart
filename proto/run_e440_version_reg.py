"""E-440 —— 版本谱系完整性回归（常驻）

防止三件事复发：
  ① versions.json 与 git tag 大幅脱节（版本级"环"不可达）
  ② 迁移到无快照版本时静默成功（代码迁了、数据没迁、不报错）
  ③ register_tag 接受不存在的 tag / 重复登记

不碰真实 manifest：全部在临时目录 + fake git 里跑。
只读地统计真实仓库的登记覆盖率（不写入）。
"""
import hashlib
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "cosmos_mem"))
import lifecycle as lc

passed = failed = 0
problems = []


def check(name, cond, detail=""):
    global passed, failed
    if cond:
        passed += 1
        print(f"  ✅ {name}{'  ' + detail if detail else ''}")
    else:
        failed += 1
        problems.append(f"{name} {detail}")
        print(f"  ❌ {name}{'  ' + detail if detail else ''}")


print("=" * 70)
print("E-440 版本谱系完整性回归")
print("=" * 70)

# ---------------------------------------------------------------- ① 真实仓库覆盖率（只读）
print("\n① 真实仓库：登记覆盖率（只读，不写入）")
real = lc.backfill_tags(dry_run=True)
total = real["total_tags"]
reg = real["registered_before"]
missing = len(real["missing"])
print(f"  git tag={total}  已登记={reg}  未登记={missing}")

# E-478：浅克隆（--depth 1）没有 tag，这不是缺陷
#
# 外部贡献者几乎都是用 `git clone --depth 1` 拿代码的，
# 那样拿不到任何 tag。原来的断言「git tag 可枚举 total > 0」
# 会判他们失败 —— 而他们什么都没做错。
#
# 检测方式：.git/shallow 存在即为浅克隆。
_shallow = os.path.exists(
    os.path.join(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))), ".git", "shallow"))

if _shallow and total == 0:
    print("  ⏭ 浅克隆环境（无 tag），跳过版本谱系断言")
    print("     要完整验证请用: git fetch --tags --unshallow")
    check("浅克隆：tag 断言跳过（非失败）", True, "shallow")
else:
    check("git tag 可枚举", total > 0, f"{total} 个")
    check("登记数 <= tag 数（一致性）", reg + missing == total)
# 这是"现状告警"而不是失败阈值：脱节本身不致命，但必须被看见
if missing > reg:
    print(f"  ⚠ 未登记({missing}) 远多于已登记({reg})"
          f" → 版本级环大多不可达，跑 `python lifecycle.py backfill --apply`")

# ---------------------------------------------------------------- ② 沙箱：fake git
tmp = tempfile.mkdtemp(prefix="e436_")
os.makedirs(os.path.join(tmp, "work"), exist_ok=True)
os.makedirs(os.path.join(tmp, "snap"), exist_ok=True)
_orig_manifest, _orig_snap = lc._MANIFEST, lc._SNAP
_orig_git = lc._git
lc._MANIFEST = os.path.join(tmp, "work", "versions.json")
lc._SNAP = os.path.join(tmp, "snap")


def fake_git(*a, **k):
    if a and a[0] == "rev-parse":
        return 0, "deadbee"
    if a and a[0] == "rev-list":
        return 0, "c0ffee1234567"
    if a and a[0] == "log":
        return 0, "1794062072"
    if a and a[0] == "tag":
        return 0, "\n".join(["T1", "T2", "T3"])
    return 0, ""


lc._git = fake_git

try:
    # ------------------------------------------------------------ ② 无 artifacts 迁移必须明说
    print("\n② 迁移到无快照版本：必须显式提示，不能静默")
    lc._save_manifest({
        "current": "A",
        "versions": [
            {"id": "A", "at": 1, "commit": "aaa111", "status": "ok",
             "reason": "", "note": "", "artifacts": {"db": "snap/A.db"}},
            {"id": "B", "at": 2, "commit": "bbb222", "status": "ok",
             "reason": "", "note": "", "artifacts": {}},
        ],
    })
    r = lc.migrate_to("B", "回归测试")
    steps = " | ".join(r["steps"])
    check("迁移本身仍成功（代码可迁）", r["ok"] is True)
    check("报告里出现'无快照'提示", "无快照" in steps or "artifacts 为空" in steps)
    check("data_migrated 标记为 False", r.get("data_migrated") is False)
    check("提示措辞说明'记忆库未回滚'", "未回滚" in steps or "保持当前" in steps)

    # 有 artifacts 时不应误报
    lc._save_manifest({
        "current": "A",
        "versions": [
            {"id": "A", "at": 1, "commit": "aaa111", "status": "ok",
             "reason": "", "note": "", "artifacts": {"db": "snap/A.db"}},
        ],
    })
    # 建一个真实的假快照文件
    snapdb = os.path.join(tmp, "snap", "A.db")
    open(snapdb, "wb").write(b"fake-db")
    orig_db = lc.VERSION_PARTS["db"]
    lc.VERSION_PARTS["db"] = os.path.join(tmp, "dst.db")
    r2 = lc.migrate_to("A", "同版本")
    check("有 artifacts 时不误报无快照",
          "无快照" not in " | ".join(r2["steps"]))
    lc.VERSION_PARTS["db"] = orig_db

    # ------------------------------------------------------------ ③ register_tag 边界
    print("\n③ register_tag 边界")
    lc._save_manifest({"current": None, "versions": []})
    e = lc.register_tag("T1")
    check("可登记已存在的 tag", e["id"] == "T1", f"commit={e['commit']}")
    check("commit 从 git 解析（非伪造）", e["commit"] == "c0ffee1")
    check("at 来自 commit 时间（非 now）", e["at"] == 1794062072)
    check("默认不带 artifacts", e["artifacts"] == {})
    check("登记后 current 不被篡改", lc._load_manifest().get("current") is None)

    try:
        lc.register_tag("T1")
        check("重复登记应被拒绝", False)
    except lc.VersionError:
        check("重复登记应被拒绝", True)

    try:
        lc.register_tag("NOPE")
        check("不存在的 tag 应被拒绝", False)
    except lc.VersionError:
        check("不存在的 tag 应被拒绝", True)

    # ------------------------------------------------------------ ④ backfill 幂等 + dry-run 不写
    print("\n④ backfill 语义")
    before = hashlib.md5(open(lc._MANIFEST, "rb").read()).hexdigest()
    d = lc.backfill_tags(dry_run=True)
    after = hashlib.md5(open(lc._MANIFEST, "rb").read()).hexdigest()
    check("dry-run 不写文件", before == after)
    check("dry-run 报出缺失项", len(d["missing"]) == 2, f"{d['missing']}")

    a1 = lc.backfill_tags(dry_run=False)
    check("apply 后新增 2 个", len(a1["newly"]) == 2, f"{a1['newly']}")
    a2 = lc.backfill_tags(dry_run=False)
    check("apply 幂等（第二次不再新增）", len(a2["newly"]) == 0)
finally:
    lc._MANIFEST, lc._SNAP = _orig_manifest, _orig_snap
    lc._git = _orig_git
    shutil.rmtree(tmp, ignore_errors=True)

# ---------------------------------------------------------------- ⑤ 真实 manifest 未被污染
print("\n⑤ 真实 manifest 未被测试污染")
cur = lc._load_manifest()
ids = [v["id"] for v in cur.get("versions", [])]
check("真实登记数仍为 8（测试在沙箱内）", len(ids) == 8, f"{len(ids)}")
check("current 仍是 E-235-baseline", cur.get("current") == "E-235-baseline")

print("\n" + "=" * 70)
print(f"{passed} 通过 / {failed} 失败")
if problems:
    print("\n失败项：")
    for p in problems:
        print("  ·", p)
print("=" * 70)
sys.exit(1 if failed else 0)
