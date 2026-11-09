"""E-324 深挖：真正的用户消息为什么没被提取到？

E-323 发现 recentUserMessages 返回空。
日志样本显示最近的 user/message 有：
  · "### 继续自主迭代"（lifecycle steer，source=cosmos-mem-lifecycle）
  · "Current runtime context..."（系统）
  · "Time sampled while preparing..."（系统）

但**真正的用户消息**（"你能看到我刚才在笔记本里面加入的一个新笔记吗"）
没有出现在样本里。

两个可能：
  A. 真正的用户消息 source 不是 'user'，被过滤了
  B. scanCursor 增量扫描从错误位置开始，跳过了新消息
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

print("=" * 76)
print("E-324 深挖用户消息提取失败")
print("=" * 76)

log_path = os.path.join(MEM, "plugin-debug.jsonl")

# ============ ① 看所有 scan-debug 样本的 source ============
print("\n① scan-debug 里所有 user/message 的 source")

records = []
with io.open(log_path, "r", encoding="utf-8", errors="replace") as f:
    for line in f.readlines()[-5000:]:
        try:
            e = json.loads(line)
            if e.get("kind") == "scan-debug":
                records.append(e)
        except Exception:
            pass

print(f"   scan-debug 记录: {len(records)} 条")

sources_seen = {}
for r in records[-10:]:
    samples = r.get("samples") or []
    for s in samples:
        shape = s.get("dataShape", "")
        # 从 shape 里提取 source.kind
        if '"source"' in shape:
            idx = shape.find('"source"')
            seg = shape[idx:idx + 80]
            print(f"     source 片段: {seg}")
            # 统计
            for kind in ["user", "cosmos-mem-lifecycle", "time-context",
                         "schedule", "subagent"]:
                if kind in seg:
                    sources_seen[kind] = sources_seen.get(kind, 0) + 1
        else:
            print(f"     无 source 字段: {shape[:80]}")

print(f"\n   出现过的 source.kind: {sources_seen}")

# ============ ② 检查典型用户消息的 source ============
print("\n② 从日志里找真正用户消息的样子")

# 用户说的话应该包含特定关键词
USER_HINTS = ["笔记本", "AGI 会是", "继续自主", "耗 token"]

found_user_msgs = []
with io.open(log_path, "r", encoding="utf-8", errors="replace") as f:
    lines = f.readlines()

for line in lines[-3000:]:
    try:
        e = json.loads(line)
    except Exception:
        continue
    # scan-debug 的 samples 里有 dataShape
    if e.get("kind") == "scan-debug":
        for s in (e.get("samples") or []):
            shape = s.get("dataShape", "")
            for h in USER_HINTS:
                if h in shape:
                    found_user_msgs.append({
                        "t": e.get("t"),
                        "hint": h,
                        "shape": shape[:200],
                    })

print(f"   找到含用户关键词的样本: {len(found_user_msgs)} 条")
for m in found_user_msgs[-5:]:
    print(f"\n     {m['t'][:19]}")
    print(f"     含关键词: {m['hint']}")
    # 提取 source
    shape = m["shape"]
    if '"source"' in shape:
        i = shape.find('"source"')
        print(f"     source: {shape[i:i+70]}")
    print(f"     内容: {shape[:100]}")

# ============ ③ 检查 scanCursor 的影响 ============
print("\n③ scanCursor 增量扫描分析")

# 从日志里找 logLen 的变化
lens = []
with io.open(log_path, "r", encoding="utf-8", errors="replace") as f:
    for line in f.readlines()[-3000:]:
        try:
            e = json.loads(line)
            if e.get("kind") == "assemble-scan":
                lens.append({
                    "t": e.get("t", "")[:19],
                    "logLen": e.get("logLen"),
                    "recentCount": e.get("recentCount"),
                })
        except Exception:
            pass

print(f"   最近 15 条 assemble-scan:")
for r in lens[-15:]:
    print(f"     {r['t']}  logLen={r['logLen']}  recentCount={r['recentCount']}")

# 分析：logLen 在增长，但 recentCount 一直是 0
if lens:
    first_len = lens[0]["logLen"]
    last_len = lens[-1]["logLen"]
    print(f"\n   logLen 变化: {first_len} → {last_len} "
          f"(+{last_len - first_len})")
    zero_count = sum(1 for r in lens[-20:] if r["recentCount"] == 0)
    print(f"   最近 20 次里 recentCount=0 的: {zero_count}")

# ============ ④ 结论 ============
print("\n" + "=" * 76)
print("结论")
print("=" * 76)

print("""
  可能的根因（需要验证）:

  1. source.kind 过滤问题
     recentUserMessages 里: if (src && src !== 'user') continue
     如果真正的用户消息 source.kind 不是 'user'，就会被过滤。

  2. scanCursor 增量扫描问题
     代码逻辑:
       let start = 0
       if (last > 0) start = Math.max(0, last - 2)
       ...
       if (sid && log.length) {
         const tail = log[log.length - 1]
         if (tail.seq) scanCursor.set(sid, tail.seq)
       }

     如果 scanCursor 记住了 log 末尾的 seq，
     下次从 (last-2) 开始扫，但新消息的 seq 可能 > last
     → 反而看不到新消息！

     这是一个**反向**的逻辑错误：
       scanCursor 记录的是"最后一条事件的 seq"，
       而不是"最后一条被处理的消息的 seq"。
       所以它永远指向末尾，下次从末尾-2 开始，
       只能看到最后 2 条事件，而不是新消息。
""")

st_issues = []
if lens and sum(1 for r in lens[-20:] if r["recentCount"] == 0) > 15:
    st_issues.append("recentCount 长期为 0")

out = {
    "experiment": "E-324",
    "sources_seen": sources_seen,
    "found_user_msgs": len(found_user_msgs),
    "log_len_change": (lens[-1]["logLen"] - lens[0]["logLen"]) if lens else 0,
    "issues": st_issues,
}
p = os.path.join(_HERE, "results", "e324_extract_fail.json")
with open(p, "w", encoding="utf-8") as f:
    json.dump(out, f, ensure_ascii=False, indent=1)
print(f"写入 results/e324_extract_fail.json")
