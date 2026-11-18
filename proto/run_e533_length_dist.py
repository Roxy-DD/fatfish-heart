"""记忆长度分布实测（FISH-15 续）

目的：找出注入时的长尾。
前一轮实测发现不同查询注入量差异很大（3071 / 7784 / 6278 字），
怀疑是某些超长记忆被捞出来了。这里直接量分布。

输出：
1. 长度分位数（p50/p90/p99/max）
2. 超长记忆清单（>1000 字的）
3. 试算「单条截断到 N 字」能省多少
"""
import sys
import warnings

warnings.filterwarnings("ignore")
sys.path.insert(0, "cosmos_mem")

from store_v2 import MemoryStore

st = MemoryStore("cosmos_mem/dsh_mem.db")

print("=" * 66)
print("记忆长度分布（FISH-15）")
print("=" * 66)

rows = st.con.execute(
    "SELECT ep_id, raw_text, cost FROM episodes "
    "WHERE raw_text IS NOT NULL AND invalid_at IS NULL"
).fetchall()

lens = sorted(len(r[1]) for r in rows)
n = len(lens)


def pct(p):
    if not n:
        return 0
    return lens[min(n - 1, int(n * p / 100))]


print(f"\n【1】总览")
print(f"  有效记忆: {n} 条")
print(f"  总字符数: {sum(lens):,}")
print(f"  平均: {sum(lens) / n:.0f} 字" if n else "")

print(f"\n【2】分位数")
for p in (50, 75, 90, 95, 99, 100):
    print(f"  p{p:3d}: {pct(p):5d} 字")

print(f"\n【3】超长记忆 TOP 10")
longest = sorted(rows, key=lambda r: -len(r[1]))[:10]
for eid, txt, cost in longest:
    print(f"  {len(txt):5d} 字  cost={cost}  {txt[:40].replace(chr(10), ' ')}")

print(f"\n【4】试算：单条截断能省多少")
print(f"  {'上限':>6} {'总字符':>12} {'节省':>10}")
base = sum(lens)
for cap in (200, 300, 400, 600, 1000):
    new_total = sum(min(l, cap) for l in lens)
    saved = base - new_total
    pct_saved = saved / base * 100 if base else 0
    print(f"  {cap:6d} {new_total:12,} {saved:9,} ({pct_saved:.1f}%)")

# ---------- 关键：注入时的实际影响 ----------
print(f"\n【5】注入时的实际影响（top_k=5 抽样）")
print("  （用真实查询看截断前后的差异）")
queries = ["记忆系统怎么优化", "继续完善记忆系统", "token 开销优化"]
for q in queries:
    hits = st.search(q, top=5)
    raw_total = 0
    cap_total = 0
    for h in hits:
        ep = h[0] if isinstance(h, tuple) else h
        t = getattr(ep, "raw_text", "") or ""
        raw_total += len(t)
        cap_total += min(len(t), 300)
    saved = raw_total - cap_total
    print(f"    {q:20s} 原 {raw_total:5d} → 截300 {cap_total:5d}  省 {saved:5d} 字")

st.con.close()

print("\n" + "=" * 66)
print("结论")
print("=" * 66)
print("""
如果 p90 远小于 max，说明少数超长记忆拉高了整体注入量。
截断到 300 字如果只能省 10-20%，那不值得（会伤质量）。
如果能省 40%+，就值得做成配置项。
""")
