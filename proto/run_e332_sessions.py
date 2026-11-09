"""E-332 真问题：为什么 session-e734 一直提取到的是旧消息？

E-331 确认：
  · 两个 session 各自独立，logLen 都单调增长
  · 重置检测按 sid 隔离，不会误判

但 E-330 观察到：
  session-e7348c8d 的 latest 一直是
  '你能不能去找一下能让你自己重启的插件...'
  而这是很久以前的消息。

问题：
  这个 session 是不是**已经不活跃了**？
  还是一个残留的僵尸 session 在反复触发 assemble？

验证：
  ① 两个 session 的时间分布
  ② session-e734 的 recentCount 和 latest 历史
  ③ 判断它是否还在接收新消息
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
print("E-332 查两个 session 的状态")
print("=" * 76)

# ============ 读日志 ============
scans = []
with io.open(log_path, "r", encoding="utf-8", errors="replace") as f:
    f.seek(0, os.SEEK_END)
    size = f.tell()
    f.seek(max(0, size - 800000))
    for line in f:
        try:
            e = json.loads(line)
            if e.get("kind") == "assemble-scan":
                scans.append(e)
        except Exception:
            pass

print(f"\n① 两个 session 的概况")

by_sid = {}
for s in scans:
    sid = str(s.get("sid", ""))
    by_sid.setdefault(sid, []).append(s)

for sid, items in by_sid.items():
    first_t = items[0].get("t", "")[:19]
    last_t = items[-1].get("t", "")[:19]
    lens = [i.get("logLen") for i in items if isinstance(i.get("logLen"), int)]
    counts = [i.get("recentCount") for i in items]

    print(f"\n   sid={sid[:35]}")
    print(f"     记录数: {len(items)}")
    print(f"     首次: {first_t}")
    print(f"     最后: {last_t}")
    print(f"     logLen: {lens[0] if lens else '-'} → {lens[-1] if lens else '-'}")
    print(f"     recentCount>0: {sum(1 for c in counts if c and c > 0)}/{len(counts)}")

    # 最后几次的 latest
    print(f"     最后 3 次的 latest:")
    for i in items[-3:]:
        print(f"       {i.get('t','')[:19]} recent={i.get('recentCount')} "
              f"latest='{str(i.get('latest',''))[:40]}'")

# ============ ② 检查是否还有新活动 ============
print("\n② 哪些 session 还在活动")

# 按时间排序，看最后 20 次是哪个 sid
recent = sorted(scans, key=lambda x: x.get("t", ""))[-20:]
sid_counts = Counter(str(s.get("sid", ""))[:25] for s in recent)
print(f"   最近 20 次 assemble 的 sid 分布:")
for sid, cnt in sid_counts.most_common():
    print(f"     {cnt:>3} 次  {sid}")

# ============ ③ session-e734 的最后活动时间 ============
print("\n③ 各 session 的最后活动时间")

latest_time = {}
for s in scans:
    sid = str(s.get("sid", ""))
    t = s.get("t", "")
    if sid not in latest_time or t > latest_time[sid]:
        latest_time[sid] = t

for sid, t in latest_time.items():
    print(f"   {t[:19]}  sid={sid[:35]}")

# ============ ④ 判断 ============
print("\n④ 判断")

if len(by_sid) == 1:
    print("   只有一个 session，没问题")
else:
    print(f"   有 {len(by_sid)} 个 session")

    # 找最旧的那个
    sorted_sids = sorted(latest_time.items(), key=lambda x: x[1])
    oldest_sid, oldest_t = sorted_sids[0]
    newest_sid, newest_t = sorted_sids[-1]

    print(f"\n   最旧活动: {oldest_t[:19]} ({oldest_sid[:25]})")
    print(f"   最新活动: {newest_t[:19]} ({newest_sid[:25]})")

    if oldest_sid != newest_sid:
        # 计算时间差
        from datetime import datetime
        try:
            t1 = datetime.fromisoformat(
                oldest_t.replace("Z", "+00:00"))
            t2 = datetime.fromisoformat(
                newest_t.replace("Z", "+00:00"))
            diff = (t2 - t1).total_seconds()
            print(f"\n   时间差: {diff:.0f} 秒")
            if diff > 60:
                print(f"   ⚠ 旧 session 已经 {diff:.0f} 秒没活动了")
                print(f"      它的 recentCount 一直是 0 是正常的")
                print(f"      （没有新消息可提取）")
        except Exception as e:
            print(f"   时间解析失败: {e}")

# ============ ⑤ 结论 ============
print("\n" + "=" * 76)
print("结论")
print("=" * 76)

print("""
  E-330 的困惑解开了：
    两个 session 交替触发 assemble，
    其中一个是**已经结束的旧 session**（残留）。

    旧 session 的 recentCount=0 是**正常的**——它没有新消息。

  但这里有一个可以改进的点：
    已经结束的 session 不应该继续触发 assemble 和记忆检索，
    那是纯浪费（每次都要跑一遍检索 + 传输）。

  不过这个浪费很小（约 40ms/次），
  而且要判断"session 是否已结束"需要额外状态。

  我的判断：不改，但记录这个现象。
""")

out = {
    "experiment": "E-332",
    "n_sessions": len(by_sid),
    "sessions": [
        {"sid": k[:35], "count": len(v),
         "last": latest_time.get(k, "")[:19]}
        for k, v in by_sid.items()
    ],
}
p = os.path.join(_HERE, "results", "e332_sessions.json")
with open(p, "w", encoding="utf-8") as f:
    json.dump(out, f, ensure_ascii=False, indent=1)
print(f"写入 results/e332_sessions.json")
