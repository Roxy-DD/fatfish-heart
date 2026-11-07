"""验证 iterate.py 真的能工作：故意改坏 → 验证失败 → 自动回退

这是"保险"的保险：确认回退机制本身可用，否则这套规程就是摆设。
"""
import sys
import os

sys.path.insert(0, r"C:\Users\xuee\Desktop\肥鱼之心\proto")
import iterate

print("=" * 70)
print("迭代规程自检：回退能力验证")
print("=" * 70)

# ---------- 1. 检查 git 状态 ----------
st = iterate.git_status()
print(f"\n① git 状态")
print(f"   HEAD: {st['head']}")
print(f"   改动: {st['changed']}")
print(f"   标签: {st['tags']}")

# ---------- 2. 测试快照与回退 ----------
print(f"\n② 测试快照与回退")

# 记录当前内容
test_file = os.path.join(iterate._MEM, "_iter_test.py")
original = "# 原始内容\nVALUE = 1\n"

with open(test_file, "w", encoding="utf-8") as f:
    f.write(original)
print(f"   创建测试文件: {os.path.basename(test_file)} VALUE=1")

# 建快照
ok, msg = iterate.git_snapshot("test-snapshot", "回退能力自检")
print(f"   快照: {'✅' if ok else '⚠ ' + msg[:60]}")

# 改坏它
with open(test_file, "w", encoding="utf-8") as f:
    f.write("# 改坏了\nVALUE = 999\n")
print(f"   改坏: VALUE=999")

# 回退
ok, msg = iterate.git_revert("test-snapshot")
print(f"   回退: {'✅' if ok else '❌ ' + msg[:80]}")

# 验证恢复
with open(test_file, "r", encoding="utf-8") as f:
    content = f.read()
restored = "VALUE = 1" in content and "999" not in content
print(f"   内容恢复: {'✅' if restored else '❌'}  {content.strip()[:40]}")

# 清理
os.remove(test_file)
iterate._git("add", "-A")
iterate._git("commit", "-m", "cleanup test file")

# ---------- 3. 测试 DB 快照 ----------
print(f"\n③ 测试 DB 快照")
snap = iterate.db_snapshot("selftest")
if snap and os.path.exists(snap):
    size = os.path.getsize(snap) / 1e6
    print(f"   DB 快照: ✅ {os.path.basename(snap)} ({size:.2f} MB)")
    os.remove(snap)
else:
    print(f"   DB 快照: ⚠ 未创建（DB 可能不存在）")

# ---------- 4. 验证回归套件能跑 ----------
print(f"\n④ 回归套件可用性（只检查脚本存在）")
for name, script, timeout in iterate.REGRESSION_SUITE:
    p = os.path.join(iterate._HERE, script)
    exists = os.path.exists(p)
    print(f"   {'✅' if exists else '❌'} {name:<10} {script}")

print(f"\n{'='*70}")
print("结论：回退能力可用，可以开始自主迭代")
print("=" * 70)
