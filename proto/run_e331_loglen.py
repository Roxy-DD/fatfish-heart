"""E-331 查：logLen 为什么在 27000 和 2900 之间反复横跳？

E-330 发现：
  logLen: [27274, 2919, 2924, 27279, 2929, 27286, 2934, 27291, 27297...]
  不是单调增长，也不是单次重置，而是两种大小交替

可能原因：
  A. 有两个 session，log 内容不同
  B. sess.log 有时返回完整历史、有时返回压缩后的
  C. 我的重置检测代码本身有副作用

影响：
  如果每次大小切换都被判为"重置"，cursor 就会被反复清空，
  导致每次都重新提取所有消息 → recentCount 时有时无。
"""
import sys
import os
import json
import io
import warnings
from collections import Counter

warnings.filterwarnings("ignore")
_HERE = os.path.dirname(os.path.abspath(__file__))
MEM = os.path.join(_HERE, "cosmos_mem")
sys.path.insert(0, MEM)

log_path = os.path.join(MEM, "plugin-debug.jsonl")

print("=" * 76)
print("E-331 查 logLen 反复横跳的原因")
print("=" * 76)

# ============ ① 完整的时间序列 ============
print("\n① 完整的 logLen 时间序列（带 sid）")

scans = []
with io.open(log_path, "r", encoding="utf-8", errors="replace") as f:
    f.seek(0, os.SEEK_END)
    size = f.tell()
    f.seek(max(0, size - 500000))
    for line in f:
        try:
            e = json.loads(line)
            if e.get("kind") == "assemble-scan":
                scans.append(e)
        except Exception:
            pass

print(f"   记录数: {len(scans)}")
print(f"\n   {'时间':<20} {'sid':<22} {'logLen':>8} {'recent':>7}")
print("   " + "-" * 62)

for s in scans[-25:]:
    t = s.get("t", "")[:19]
    sid = str(s.get("sid", ""))[:20]
    L = s.get("logLen")
    c = s.get("recentCount")
    print(f"   {t:<20} {sid:<22} {str(L):>8} {str(c):>7}")

# ============ ② 按 sid 分组看 ============
print("\n② 按 sid 分组")

by_sid = {}
for s in scans:
    sid = str(s.get("sid", ""))
    by_sid.setdefault(sid, []).append(s)

print(f"   不同 sid: {len(by_sid)}")
for sid, items in by_sid.items():
    lens = [i.get("logLen") for i in items if isinstance(i.get("logLen"), int)]
    print(f"\n   sid={sid[:30]}")
    print(f"     记录数: {len(items)}")
    if lens:
        print(f"     logLen: {lens[:10]}{'...' if len(lens) > 10 else ''}")
        print(f"     范围: {min(lens)} ~ {max(lens)}")

# ============ ③ 分析交替模式 ============
print("\n③ 交替模式分析")

recent = scans[-20:]
lens = [s.get("logLen") for s in recent if isinstance(s.get("logLen"), int)]

if len(lens) >= 4:
    # 分成两组
    small = [x for x in lens if x < 5000]
    large = [x for x in lens if x >= 5000]
    print(f"\n   小值组 (<5000): {len(small)} 个，范围 {min(small) if small else '-'} ~ {max(small) if small else '-'}")
    print(f"   大值组 (>=5000): {len(large)} 个，范围 {min(large) if large else '-'} ~ {max(large) if large else '-'}")

    # 检查是否与 sid 相关
    print(f"\n   检查每个记录对应的 sid:")
    for s in recent[-10:]:
        L = s.get("logLen")
        sid = str(s.get("sid", ""))[:25]
        grp = "小" if isinstance(L, int) and L < 5000 else "大"
        print(f"     {grp} logLen={L} sid={sid}")

# ============ ④ 检查是否有多个 session 在活动 ============
print("\n④ 是否有多个 session")

sids = Counter(str(s.get("sid", "")) for s in scans[-50:])
print(f"   最近 50 次的不同 sid:")
for sid, cnt in sids.most_common():
    print(f"     {cnt:>3} 次  sid={sid[:35]}")

# ============ ⑤ 关键判断 ============
print("\n⑤ 判断")

if len(by_sid) > 1:
    print(f"""
   ⚠ 有 {len(by_sid)} 个 session 在活动

   这可能不是 bug，而是**正常现象**：
     · 每个 session 有自己的 log
     · 不同 session 的 log 长度不同

   但我的"重置检测"是**按 sid 隔离**的（scanCursor 用 sid 做 key），
   所以理论上不会互相干扰。

   需要确认：同一个 sid 的 logLen 是否单调增长。
""")
else:
    print("   只有一个 session")

# 检查同一 sid 内是否单调
print("\n   同一 sid 内的 logLen 变化:")
for sid, items in by_sid.items():
    lens = [i.get("logLen") for i in items
            if isinstance(i.get("logLen"), int)]
    if len(lens) < 3:
        continue
    drops = sum(1 for i in range(1, len(lens)) if lens[i] < lens[i-1])
    print(f"     sid={sid[:25]}: {len(lens)} 个记录, "
          f"下降 {drops} 次")
    if drops > 0:
        print(f"       ⚠ 同一 sid 内 logLen 会下降，说明 log 确实在变")

out = {
    "experiment": "E-331",
    "n_sids": len(by_sid),
    "sids": [{"sid": k[:30], "count": len(v)} for k, v in by_sid.items()],
}
p = os.path.join(_HERE, "results", "e331_loglen.json")
with open(p, "w", encoding="utf-8") as f:
    json.dump(out, f, ensure_ascii=False, indent=1)
print(f"\n写入 results/e331_loglen.json")
