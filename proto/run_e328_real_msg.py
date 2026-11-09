"""E-328 抓真实的用户消息结构

E-327 确认顺序逻辑正确，但真实 latest 是旧消息。
说明：真实 log 里最近 8 条 user/message 都是老消息。

要查清：
  ① 用户刚说的这句话（"你能看到我刚才在笔记本..."）
     在 log 里的 source.kind 是什么
  ② 它是否被 source 过滤掉了
  ③ 它是否被某些前缀规则过滤掉了

方法：从日志里搜索这句真实用户消息，看它的完整结构。
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
print("E-328 抓真实用户消息的结构")
print("=" * 76)

# ============ ① 在日志里搜真实用户消息 ============
print("\n① 搜索真实用户消息")

USER_SENTENCES = [
    "你能看到我刚才在笔记本里面加入的一个新笔记",
    "主要是B",
    "他说的是不必无损迁移",
    "可以现在先整理一下记忆",
    "现在的记忆有点耗 token",
]

if not os.path.exists(log_path):
    print("   日志不存在")
    raise SystemExit(1)

with io.open(log_path, "r", encoding="utf-8", errors="replace") as f:
    all_lines = f.readlines()

print(f"   日志总行数: {len(all_lines)}")

for sent in USER_SENTENCES:
    found = []
    for i, line in enumerate(all_lines):
        if sent in line:
            found.append((i, line))
    if found:
        print(f"\n   ✅ 找到 '{sent[:20]}...' 在 {len(found)} 处")
        # 显示最后一处的结构
        idx, line = found[-1]
        try:
            e = json.loads(line)
            kind = e.get("kind")
            print(f"      行 {idx+1}, kind={kind}")
            if kind == "scan-debug":
                for s in (e.get("samples") or []):
                    shape = s.get("dataShape", "")
                    if sent[:10] in shape:
                        print(f"      样本结构: {shape[:250]}")
            else:
                # 其他类型的日志
                txt = str(e)[:200]
                print(f"      内容: {txt}")
        except Exception:
            print(f"      无法解析: {line[:150]}")
    else:
        print(f"   ❌ 未找到 '{sent[:20]}...'")

# ============ ② 看最近的 scan-debug 样本 ============
print("\n② 最近的 scan-debug 样本结构")

records = []
for i, line in enumerate(all_lines):
    try:
        e = json.loads(line)
        if e.get("kind") == "scan-debug":
            records.append((i, e))
    except Exception:
        pass

print(f"   scan-debug 总数: {len(records)}")

for idx, r in records[-3:]:
    print(f"\n   行 {idx+1} @ {r.get('t','')[:19]} "
          f"foundUserMessage={r.get('foundUserMessage')}")
    for s in (r.get("samples") or []):
        shape = s.get("dataShape", "")
        print(f"     dataKeys: {s.get('dataKeys')}")
        print(f"     shape: {shape[:180]}")

# ============ ③ 检查 seq 的范围 ============
print("\n③ 检查日志里 user/message 的 seq")

# scan-debug 里没记 seq，那从 assemble-scan 的 logLen 推断
scans = []
for line in all_lines:
    try:
        e = json.loads(line)
        if e.get("kind") == "assemble-scan":
            scans.append(e)
    except Exception:
        pass

if scans:
    print(f"   最近 logLen: {[s.get('logLen') for s in scans[-5:]]}")
    print(f"   最近 recentCount: "
          f"{[s.get('recentCount') for s in scans[-5:]]}")

# ============ 结论 ============
print("\n" + "=" * 76)
print("结论")
print("=" * 76)

found_count = 0
for sent in USER_SENTENCES:
    if any(sent in line for line in all_lines):
        found_count += 1

print(f"""
  找到的真实用户消息: {found_count}/{len(USER_SENTENCES)}

  如果找到了但 latest 仍是旧的，可能原因：
    · 那条消息的 source.kind 不是 'user'（被过滤）
    · 那条消息在 log 里的位置太靠前（超出 reviewWindow=8）

  如果根本没找到，说明：
    · scan-debug 只在 recent 为空时打印样本
    · 现在 recent 有值（8 条），所以不打印样本了
    · 我们看不到新消息的结构
""")

out = {
    "experiment": "E-328",
    "found": found_count,
    "total": len(USER_SENTENCES),
    "scan_debug_count": len(records),
}
p = os.path.join(_HERE, "results", "e328_real_msg.json")
with open(p, "w", encoding="utf-8") as f:
    json.dump(out, f, ensure_ascii=False, indent=1)
print(f"写入 results/e328_real_msg.json")
