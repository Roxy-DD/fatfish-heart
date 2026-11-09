"""E-330 真实环境验证：消息提取修复后能否拿到当前对话

E-329 修复了 log 重置导致的 cursor 失效。
但只在单元测试里验证过，**没在真实环境验证**。

关键验证：
  ① 当前 assemble 用的 query 是不是最近说的话
  ② recentCount 是否大于 0
  ③ 检索结果是否与当前话题相关

同时检查副作用：
  ④ log 重置检测会不会误判（正常增长被当成重置）
"""
import sys
import os
import json
import io
import time
import warnings

warnings.filterwarnings("ignore")
_HERE = os.path.dirname(os.path.abspath(__file__))
MEM = os.path.join(_HERE, "cosmos_mem")
sys.path.insert(0, MEM)

log_path = os.path.join(MEM, "plugin-debug.jsonl")

print("=" * 76)
print("E-330 真实环境验证消息提取修复")
print("=" * 76)


def read_log(kind, n=20):
    out = []
    if not os.path.exists(log_path):
        return out
    with io.open(log_path, "r", encoding="utf-8", errors="replace") as f:
        f.seek(0, os.SEEK_END)
        size = f.tell()
        f.seek(max(0, size - 500000))
        for line in f:
            try:
                e = json.loads(line)
                if e.get("kind") == kind:
                    out.append(e)
            except Exception:
                pass
    return out[-n:]


# ============ ① 当前 assemble 的 query ============
print("\n① 最近的 assemble query")

assembles = read_log("assemble", 10)
print(f"   记录数: {len(assembles)}")

queries = []
for a in assembles:
    q = str(a.get("query", ""))
    if q not in queries:
        queries.append(q)
    print(f"     {a.get('t','')[:19]}  hits={a.get('hits')}  "
          f"'{q[:50]}'")

# ============ ② scan 的 recentCount ============
print("\n② 最近的 recentCount")

scans = read_log("assemble-scan", 15)
print(f"   记录数: {len(scans)}")

nonzero = 0
for s in scans:
    cnt = s.get("recentCount", 0)
    if cnt > 0:
        nonzero += 1
    print(f"     {s.get('t','')[:19]}  recentCount={cnt}  "
          f"latest='{str(s.get('latest',''))[:35]}'")

print(f"\n   recentCount > 0 的次数: {nonzero}/{len(scans)}")

# ============ ③ 判断修复是否生效 ============
print("\n③ 修复效果判断")

# 找出最近的用户消息关键词（从我自己这轮的指令推断）
# 用户最近说过的应该是关于"汇报总结"、"继续"
RECENT_HINTS = ["汇报", "总结", "继续自主迭代", "验证"]

has_recent = any(
    any(h in str(s.get("latest", "")) for h in RECENT_HINTS)
    for s in scans if s.get("recentCount", 0) > 0
)

print(f"   最近的 latest 里含当前话题关键词: {has_recent}")

if nonzero > 0:
    print(f"   ✅ recentCount 有值（{nonzero} 次），提取在工作")
else:
    print(f"   ⚠ recentCount 全为 0 —— 可能还没有新对话")

# ============ ④ 检查 log 重置检测是否误判 ============
print("\n④ 检查 log 重置检测的误判风险")

# 看 logLen 序列
lens = [s.get("logLen") for s in scans if isinstance(s.get("logLen"), int)]
print(f"   logLen 序列: {lens}")

if len(lens) >= 2:
    # 正常增长 vs 重置
    drops = []
    for i in range(1, len(lens)):
        if lens[i] < lens[i-1] * 0.5 and lens[i-1] > 1000:
            drops.append((i, lens[i-1], lens[i]))
    print(f"\n   突降次数: {len(drops)}")
    for idx, before, after in drops:
        print(f"     位置 {idx}: {before} → {after}")

    if not drops:
        print("   ✅ 无突降，重置检测不会误触发")

# ============ ⑤ 检查全局重置计数 ============
print("\n⑤ 全局重置计数（我在代码里加的）")

# 这个值在插件进程里，通过日志看不到
# 但可以检查是否有相关日志
reset_logs = []
with io.open(log_path, "r", encoding="utf-8", errors="replace") as f:
    f.seek(0, os.SEEK_END)
    size = f.tell()
    f.seek(max(0, size - 300000))
    for line in f:
        if "scan" in line.lower() and "reset" in line.lower():
            reset_logs.append(line.strip()[:150])

print(f"   含 scan+reset 的日志行: {len(reset_logs)}")
for l in reset_logs[-3:]:
    print(f"     {l}")

# ============ 结论 ============
print("\n" + "=" * 76)
print("结论")
print("=" * 76)

print(f"""
  最近 assemble 查询: {len(queries)} 个不同
  最近 recentCount>0: {nonzero}/{len(scans)}
  含当前话题关键词: {has_recent}
  log 突降次数: {len(drops) if len(lens) >= 2 else 'N/A'}

  {'✅ 修复生效：能提取新消息' if nonzero > 0 else '⚠ 还需观察'}
""")

out = {
    "experiment": "E-330",
    "n_queries": len(queries),
    "recent_nonzero": nonzero,
    "has_recent_topic": has_recent,
    "log_drops": len(drops) if len(lens) >= 2 else 0,
}
p = os.path.join(_HERE, "results", "e330_real_verify.json")
with open(p, "w", encoding="utf-8") as f:
    json.dump(out, f, ensure_ascii=False, indent=1)
print(f"写入 results/e330_real_verify.json")
