"""E-312 修复：合并导致内容无限堆积（最长 6637 字）

E-311 发现的问题：
  一条记忆被合并到 6637 字（原本平均约 100 字）

根因：
  _append_merged 每次合并都往 keep 后面追加「｜合并: ...」
  当一条记忆被合并 N 次，长度就是 N 倍
  没有长度上限控制

后果：
  ① 检索时这条记忆占满注入预算
  ② 向量语义被稀释（变成大杂烩）
  ③ 阅读困难

修复方向：
  A. 合并时设置长度上限，超过就只记"合并了N条"而不是全文
  B. 用摘要而不是全文追加
  C. 超长时自动拆分为独立记忆（但那等于没合并）

选 A：控制单条记忆的合并后长度上限，超出部分折叠为引用
"""
import sys
import os
import json
import warnings

warnings.filterwarnings("ignore")
_HERE = os.path.dirname(os.path.abspath(__file__))
MEM = os.path.join(_HERE, "cosmos_mem")
sys.path.insert(0, MEM)

from store_v2 import MemoryStore

DB = os.path.join(MEM, "dsh_mem.db")
st = MemoryStore(DB)

print("=" * 76)
print("E-312 诊断合并导致的内容堆积")
print("=" * 76)

# ============ ① 找出最长的记忆 ============
print("\n① 最长的记忆")

rows = st.con.execute(
    "SELECT ep_id, raw_text, length(raw_text) as n FROM episodes "
    "WHERE invalid_at IS NULL ORDER BY n DESC LIMIT 10").fetchall()

print(f"\n   {'长度':>6} {'合并次数':>8}  {'ep_id':<10} 内容开头")
for eid, txt, n in rows:
    mc = txt.count("｜合并:")
    print(f"   {n:>6} {mc:>8}  [{eid[:8]}] {txt[:50]}")

# ============ ② 长度分布 ============
print("\n② 长度分布")

all_lens = [r[0] for r in st.con.execute(
    "SELECT length(raw_text) FROM episodes WHERE invalid_at IS NULL").fetchall()]

import statistics
if all_lens:
    print(f"   条数: {len(all_lens)}")
    print(f"   最小: {min(all_lens)}")
    print(f"   中位: {statistics.median(all_lens):.0f}")
    print(f"   均值: {sum(all_lens)/len(all_lens):.0f}")
    print(f"   最大: {max(all_lens)}")

    # 分档
    buckets = {
        "0-200": 0, "200-500": 0, "500-1000": 0,
        "1000-2000": 0, "2000+": 0,
    }
    for n in all_lens:
        if n < 200:
            buckets["0-200"] += 1
        elif n < 500:
            buckets["200-500"] += 1
        elif n < 1000:
            buckets["500-1000"] += 1
        elif n < 2000:
            buckets["1000-2000"] += 1
        else:
            buckets["2000+"] += 1

    print(f"\n   长度分布:")
    for k in ["0-200", "200-500", "500-1000", "1000-2000", "2000+"]:
        n = buckets[k]
        bar = "█" * min(n, 40)
        print(f"     {k:<10} {n:>4}  {bar}")

    over_1000 = sum(1 for n in all_lens if n > 1000)
    print(f"\n   >1000 字: {over_1000} 条 "
          f"({over_1000/len(all_lens)*100:.0f}%)")

# ============ ③ 看最长那条的完整内容 ============
print("\n③ 最长记忆的完整内容（前 1000 字）")

if rows:
    eid, txt, n = rows[0]
    print(f"\n   [{eid}] 共 {n} 字")
    print(f"   {'-'*70}")
    # 按合并标记分段显示
    parts = txt.split("｜合并:")
    for i, p in enumerate(parts[:8]):
        prefix = "主体" if i == 0 else f"合并{i}"
        print(f"   [{prefix}] {p.strip()[:80]}")
    if len(parts) > 8:
        print(f"   ... 还有 {len(parts) - 8} 段")

# ============ ④ 检查注入影响 ============
print("\n④ 对注入的影响")

BUDGET = 1500
if rows:
    longest = rows[0][2]
    print(f"   最长记忆: {longest} 字")
    print(f"   注入预算: {BUDGET} 字")
    if longest > BUDGET:
        print(f"   ⚠ 单条记忆就超出预算 {longest - BUDGET} 字")
        print(f"      → 这条一旦被检索到，会挤掉所有其他记忆")

# ============ ⑤ 检查检索时是否真的会注入这么长 ============
print("\n⑤ 检索是否会返回超长记忆")

# 用一个可能与最长记忆相关的查询
if rows:
    eid, txt, n = rows[0]
    # 取开头几个词做查询
    keywords = txt[:30]
    res = st.search(keywords, top=3)
    print(f"\n   查询: {keywords[:30]}...")
    for ep, sc, fl in res[:3]:
        print(f"     cos={sc:.2f} {len(ep.raw_text):>5}字 "
              f"[{ep.ep_id[:8]}] {ep.raw_text[:40]}")

st.con.close()

# ============ 结论 ============
print("\n" + "=" * 76)
print("结论")
print("=" * 76)

issues = []
if all_lens:
    over = sum(1 for n in all_lens if n > 1000)
    if over > 0:
        issues.append(f"{over} 条记忆超过 1000 字（合并堆积）")
    if max(all_lens) > BUDGET:
        issues.append(f"最长记忆 {max(all_lens)} 字，超出注入预算")

print(f"""
  记忆条数: {len(all_lens)}
  最长: {max(all_lens) if all_lens else 0} 字
  超过 1000 字的: {sum(1 for n in all_lens if n > 1000) if all_lens else 0} 条

  根因：
    _append_merged 每次合并都追加全文，无长度上限。
    一条记忆被合并 N 次，长度就是 N 倍。

  需要修：合并时加上限，超出部分折叠为引用。
""")

if issues:
    print("  问题：")
    for i in issues:
        print(f"    · {i}")

out = {
    "experiment": "E-312",
    "n_memories": len(all_lens),
    "max_len": max(all_lens) if all_lens else 0,
    "median_len": statistics.median(all_lens) if all_lens else 0,
    "over_1000": sum(1 for n in all_lens if n > 1000) if all_lens else 0,
    "issues": issues,
}
p = os.path.join(_HERE, "results", "e312_bloat.json")
with open(p, "w", encoding="utf-8") as f:
    json.dump(out, f, ensure_ascii=False, indent=1)
print(f"写入 results/e312_bloat.json")
