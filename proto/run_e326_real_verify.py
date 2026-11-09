"""E-326 验证：修复后真实环境能否提取到新的用户消息

关键验证点：
  重载后 scanCursor 被重置（模块级 Map）
  下一次 assemble 应该能提取到**最新**的用户消息
  而不是继续用旧 query

方法：
  ① 记录当前 marker 和 scanCursor 状态
  ② 等下一轮 assemble
  ③ 检查 assemble 的 query 是否是我们最近说的话
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
print("E-326 验证真实环境的消息提取")
print("=" * 76)


def read_recent(kind, n=10, tail_bytes=400000):
    """读最近的某类日志"""
    if not os.path.exists(log_path):
        return []
    with io.open(log_path, "r", encoding="utf-8", errors="replace") as f:
        f.seek(0, os.SEEK_END)
        size = f.tell()
        f.seek(max(0, size - tail_bytes))
        lines = f.readlines()
    out = []
    for line in lines:
        try:
            e = json.loads(line)
            if e.get("kind") == kind:
                out.append(e)
        except Exception:
            pass
    return out[-n:]


# ============ ① 当前状态 ============
print("\n① 当前状态")

scans = read_recent("assemble-scan", 10)
print(f"   最近 assemble-scan: {len(scans)} 条")
for s in scans[-5:]:
    print(f"     {s.get('t','')[:19]}  recentCount={s.get('recentCount')}  "
          f"latest='{str(s.get('latest',''))[:40]}'")

n_nonzero = sum(1 for s in scans if s.get("recentCount", 0) > 0)
print(f"\n   最近 10 次里 recentCount>0: {n_nonzero}")

# ============ ② 等下一轮 ============
print("\n② 等下一轮 assemble（最多 90 秒）")

before_n = len(read_recent("assemble-scan", 100))
before_q = None
scans_before = read_recent("assemble-scan", 3)
if scans_before:
    before_q = scans_before[-1].get("latest")

print(f"   等待前最后一条 latest: '{str(before_q)[:45]}'")
print(f"   等待前记录数: {before_n}")

found_new = False
for i in range(18):   # 18 * 5s = 90s
    time.sleep(5)
    scans_now = read_recent("assemble-scan", 100)
    if len(scans_now) > before_n:
        newest = scans_now[-1]
        cnt = newest.get("recentCount", 0)
        latest = str(newest.get("latest", ""))[:60]
        print(f"\n   ✅ 检测到新一轮（第 {i*5+5} 秒）")
        print(f"      recentCount={cnt}")
        print(f"      latest='{latest}'")
        if cnt > 0:
            found_new = True
            print(f"      → ✅ 成功提取到用户消息！")
        else:
            print(f"      → ⚠ recentCount 仍为 0")
        break
else:
    print(f"\n   （90 秒内没有新的 assemble 记录）")

# ============ ③ 检查 assemble 的 query ============
print("\n③ 检查 assemble 的 query 是否更新")

assembles = read_recent("assemble", 5)
print(f"   最近 assemble: {len(assembles)} 条")

unique_queries = set()
for a in assembles:
    q = str(a.get("query", ""))
    unique_queries.add(q)
    print(f"     {a.get('t','')[:19]}  hits={a.get('hits')}  "
          f"query='{q[:45]}'")

print(f"\n   不同 query 数: {len(unique_queries)}")

# ============ 结论 ============
print("\n" + "=" * 76)
print("结论")
print("=" * 76)

old_query = "你能不能去找一下能让你自己重启的插件"
is_stale = all(old_query in q for q in unique_queries) and len(unique_queries) == 1

print(f"""
  提取到新消息: {found_new}
  不同 query 数: {len(unique_queries)}
  是否仍用旧 query: {is_stale}
""")

if found_new and not is_stale:
    print("  ✅ 修复生效：能提取新消息，query 在更新")
    verdict = "fixed"
elif not is_stale:
    print("  ✅ query 在变化（修复生效）")
    verdict = "fixed"
elif found_new:
    print("  🟡 能提取但 query 还是旧的（可能需要下一轮）")
    verdict = "partial"
else:
    print("  ⚠ 还没看到新消息（可能还没有新的用户输入）")
    print("     需要用户实际发一条消息才能验证")
    verdict = "waiting"

out = {
    "experiment": "E-326",
    "found_new": found_new,
    "unique_queries": len(unique_queries),
    "is_stale": is_stale,
    "verdict": verdict,
}
p = os.path.join(_HERE, "results", "e326_real_verify.json")
with open(p, "w", encoding="utf-8") as f:
    json.dump(out, f, ensure_ascii=False, indent=1)
print(f"写入 results/e326_real_verify.json")
