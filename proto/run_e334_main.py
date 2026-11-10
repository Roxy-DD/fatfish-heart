"""E-334 关键疑点：为什么当前会话的 recentCount 一直是 0？

E-332/333 数据显示：
  session-7413467a（当前会话）  351 条记录，recentCount>0 的次数很少
  session-e7348c8d（另一会话）   75 条记录，recentCount>0: 9/76

如果当前会话一直提取不到消息 → 检索仍用旧 query
→ 修复对我自己没生效。

必须查清：
  ① 当前会话的 recentCount 历史
  ② 当前会话的 logLen 与 seq 情况
  ③ 是否是 cursor 卡住了
"""
import sys
import os
import json
import io
import warnings

warnings.filterwarnings("ignore")
_HERE = os.path.dirname(os.path.abspath(__file__))
MEM = os.path.join(_HERE, "cosmos_mem")
sys.path.insert(0, MEM)

log_path = os.path.join(MEM, "plugin-debug.jsonl")

print("=" * 76)
print("E-334 当前会话的 recentCount 为什么是 0")
print("=" * 76)

# ============ ① 按 sid 统计 recentCount ============
print("\n① 按 sid 统计")

scans = []
with io.open(log_path, "r", encoding="utf-8", errors="replace") as f:
    f.seek(0, os.SEEK_END)
    size = f.tell()
    f.seek(max(0, size - 1200000))
    for line in f:
        try:
            e = json.loads(line)
            if e.get("kind") == "assemble-scan":
                scans.append(e)
        except Exception:
            pass

by_sid = {}
for s in scans:
    sid = str(s.get("sid", ""))
    by_sid.setdefault(sid, []).append(s)

print(f"   总记录: {len(scans)}, 不同 sid: {len(by_sid)}")

for sid, items in by_sid.items():
    counts = [i.get("recentCount", 0) for i in items]
    nz = sum(1 for c in counts if c > 0)
    print(f"\n   sid={sid[:32]}")
    print(f"     记录 {len(items)} 条")
    print(f"     recentCount>0: {nz} 次 ({nz/len(items)*100:.1f}%)")
    # 最后 15 次的 recentCount
    tail = counts[-15:]
    print(f"     最后 15 次: {tail}")

# ============ ② 当前会话的最后几条详细看 ============
print("\n② 当前会话的最近记录详情")

# 找记录最多的那个（应该是当前会话）
main_sid = max(by_sid, key=lambda k: len(by_sid[k]))
print(f"   主会话: {main_sid[:40]}")

for s in by_sid[main_sid][-10:]:
    t = s.get("t", "")[:19]
    L = s.get("logLen")
    c = s.get("recentCount")
    lat = str(s.get("latest", ""))[:40]
    print(f"     {t}  logLen={L:>6}  recent={c}  '{lat}'")

# ============ ③ 检查 seq 情况（从 logLen 推断）============
print("\n③ 推测：是否 cursor 卡在很大值")

# 如果 logLen 一直很大（27000+），而 cursor 也很大，
# 那么新消息的 seq 必须 > cursor 才能被提取
# 但 seq 增长速度取决于事件数量

lens = [s.get("logLen") for s in by_sid[main_sid]
        if isinstance(s.get("logLen"), int)]
if len(lens) >= 2:
    print(f"   logLen: {lens[0]} → {lens[-1]} (+{lens[-1]-lens[0]})")
    print(f"   最近 10 次: {lens[-10:]}")

# 关键问题：log 里到底有没有新的 user/message
print("\n④ 检查最近是否有新的 user/message 事件")

# scan-debug 样本能告诉我们
scan_debugs = []
with io.open(log_path, "r", encoding="utf-8", errors="replace") as f:
    f.seek(0, os.SEEK_END)
    size = f.tell()
    f.seek(max(0, size - 600000))
    for line in f:
        try:
            e = json.loads(line)
            if e.get("kind") == "scan-debug":
                scan_debugs.append(e)
        except Exception:
            pass

print(f"   scan-debug 记录: {len(scan_debugs)}")
print(f"   （只在 recent 为空时才打印，所以有记录说明确实提取失败）")

for s in scan_debugs[-3:]:
    t = s.get("t", "")[:19]
    nth = s.get("nth")
    found = s.get("foundUserMessage")
    print(f"\n     {t}  nth={nth}  foundUserMessage={found}")
    for sample in (s.get("samples") or []):
        shape = str(sample.get("dataShape", ""))[:120]
        print(f"       {shape}")

# ============ ⑤ 判断 ============
print("\n⑤ 判断")

main_items = by_sid[main_sid]
tail_counts = [i.get("recentCount", 0) for i in main_items[-20:]]
all_zero = all(c == 0 for c in tail_counts)

print(f"""
   主会话最后 20 次 recentCount: {tail_counts}
   全部为 0: {all_zero}

   如果全部为 0，说明当前会话的消息提取确实失败了。
""")

if all_zero:
    print("""   ⚠ 确认：当前会话的提取失败

   可能原因（需进一步验证）：
     A. scanCursor 卡在一个很大的 seq 上
     B. 当前会话 log 里的 user/message 的 seq 都 <= cursor
     C. 当前会话的 log 结构与其他不同（比如 source.kind 不是 'user'）

   注意：session-e734 能提取到，说明代码本身没问题，
        是当前会话的**数据状态**有问题。
""")
else:
    print("   ✅ 最近有提取成功")

out = {
    "experiment": "E-334",
    "main_sid": main_sid[:40],
    "main_records": len(main_items),
    "tail_counts": tail_counts,
    "all_zero": all_zero,
}
p = os.path.join(_HERE, "results", "e334_main_session.json")
with open(p, "w", encoding="utf-8") as f:
    json.dump(out, f, ensure_ascii=False, indent=1)
print(f"\n写入 results/e334_main_session.json")
