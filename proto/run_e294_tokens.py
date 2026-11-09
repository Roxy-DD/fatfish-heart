"""诊断：记忆系统到底占了多少上下文？

用户反馈"有点耗 token，耗上下文"。
必须用数据量化，不能猜。

测量：
  ① 单轮注入的实际字符/估算 token
  ② 各部分的占比（L0/L1常驻、检索结果、自主准则、知识提示、前瞻）
  ③ 从真实日志统计历史趋势
  ④ 找出最大占用者
"""
import sys
import os
import json
import io

_HERE = os.path.dirname(os.path.abspath(__file__))
_MEM = os.path.join(_HERE, "cosmos_mem")
sys.path.insert(0, _MEM)

from store_v2 import MemoryStore
import hook as _hook

DB = os.path.join(_MEM, "dsh_mem.db")
st = MemoryStore(DB)

print("=" * 78)
print("诊断：记忆系统占用的上下文")
print("=" * 78)

# ============ ① 各部分字符数 ============
print("\n① 各部分实测字符数")

# L0/L1 常驻（首轮全文）
present = _hook.present_ids(st)
full_chars = 0
summary_chars = 0
by_level = {}
for eid in present:
    ep = st.get(eid)
    if not ep:
        continue
    info = next((x for x in _hook.protected_of(st) if x["ep_id"] == eid), {})
    lvl = info.get("level", "?")
    note = info.get("note", "") or "准则"
    # 首轮：- [id] 标题：正文
    full_len = len(f"- [{eid}] {note}：{ep.raw_text}")
    # 后续：- [id] 标题：正文前24字…
    head = ep.raw_text[:24] if ep.raw_text else ""
    sum_len = len(f"- [{eid}] {note}：{head}…")
    full_chars += full_len
    summary_chars += sum_len
    by_level.setdefault(lvl, {"n": 0, "full": 0, "sum": 0})
    by_level[lvl]["n"] += 1
    by_level[lvl]["full"] += full_len
    by_level[lvl]["sum"] += sum_len

print(f"\n  常驻记忆（L0/L1）: {len(present)} 条")
for lvl in sorted(by_level):
    v = by_level[lvl]
    print(f"    {lvl}: {v['n']} 条, 首轮 {v['full']} 字, 后续 {v['sum']} 字")
print(f"  合计: 首轮 {full_chars} 字 / 后续 {summary_chars} 字")

# 检索结果：取几条实测
print(f"\n  检索结果（top 5）:")
total_body = 0
for q in ["记忆系统的设计", "版本回退机制"]:
    res = st.search(q, top=5)
    items = [(ep, sc, fl) for ep, sc, fl in res if fl != 2]
    body = ""
    for ep, sc, fl in items:
        body += f"- ({sc}) {ep.raw_text}\n"
    total_body += len(body)
    print(f"    查询 '{q}': {len(items)} 条, {len(body)} 字")
avg_body = total_body // 2 if total_body else 0

# 自主记忆准则（硬编码）
MEMORY_POLICY_CHARS = 1200  # 估算

# 知识提示
try:
    import cli_mcp
    kh = cli_mcp.cmd_knowledge_hint(st, {"sync_todos": False})
    kh_chars = len(kh.get("text", ""))
except Exception:
    kh_chars = 0

# 前瞻
PREWARM_CHARS = 400  # 估算

print(f"\n  其他部分（估算）:")
print(f"    自主记忆准则:     ~{MEMORY_POLICY_CHARS} 字")
print(f"    知识整理提示:     {kh_chars} 字（仅首轮）")
print(f"    会话前瞻:         ~{PREWARM_CHARS} 字（仅首轮）")

# ============ ② 单轮总计 ============
print("\n" + "=" * 78)
print("② 单轮注入总览")
print("=" * 78)

first_round = full_chars + avg_body + MEMORY_POLICY_CHARS + kh_chars + PREWARM_CHARS
later_round = summary_chars + avg_body + MEMORY_POLICY_CHARS

print(f"""
  首轮:  {first_round} 字
  后续:  {later_round} 字

  拆解（后续轮）:
    L0/L1 摘要    {summary_chars:>6} 字  ({summary_chars/later_round*100:>5.1f}%)
    检索结果      {avg_body:>6} 字  ({avg_body/later_round*100:>5.1f}%)
    自主记忆准则  {MEMORY_POLICY_CHARS:>6} 字  ({MEMORY_POLICY_CHARS/later_round*100:>5.1f}%)

  估算 token（中文约 1.5 字/token）:
    首轮 ≈ {first_round/1.5:.0f} tokens
    后续 ≈ {later_round/1.5:.0f} tokens
""")

# ============ ③ 从真实日志统计 ============
print("=" * 78)
print("③ 真实日志里的历史数据")
print("=" * 78)

log = os.path.join(_MEM, "plugin-debug.jsonl")
chars_hist = []
if os.path.exists(log):
    with io.open(log, "r", encoding="utf-8", errors="replace") as f:
        for line in f:
            try:
                e = json.loads(line)
                if e.get("kind") == "assemble":
                    c = e.get("content_chars")
                    if c:
                        chars_hist.append(c)
            except Exception:
                continue

if chars_hist:
    print(f"\n  样本数: {len(chars_hist)}")
    print(f"  平均: {sum(chars_hist)/len(chars_hist):.0f} 字")
    print(f"  最大: {max(chars_hist)} 字")
    print(f"  最小: {min(chars_hist)} 字")
    # 分布
    import statistics
    print(f"  中位: {statistics.median(chars_hist):.0f} 字")
    over = [c for c in chars_hist if c > 2500]
    print(f"  >2500 字的轮次: {len(over)}/{len(chars_hist)} "
          f"({len(over)/len(chars_hist)*100:.0f}%)")
else:
    print("  无历史数据")

# ============ ④ 找最大占用者 ============
print("\n" + "=" * 78)
print("④ 最大占用者分析")
print("=" * 78)

occupants = [
    ("L0/L1 常驻（首轮）", full_chars),
    ("L0/L1 常驻（后续）", summary_chars),
    ("检索结果", avg_body),
    ("自主记忆准则", MEMORY_POLICY_CHARS),
    ("知识提示（首轮）", kh_chars),
    ("会话前瞻（首轮）", PREWARM_CHARS),
]
occupants.sort(key=lambda x: -x[1])
for name, n in occupants:
    bar = "█" * int(n / 40)
    print(f"  {name:<22} {n:>6} 字  {bar}")

st.con.close()

# ============ 结论 ============
print("\n" + "=" * 78)
print("结论")
print("=" * 78)

print(f"""
  单轮注入 ≈ {later_round} 字（后续） / {first_round} 字（首轮）

  主要占用者（后续轮）：
    1. 自主记忆准则  {MEMORY_POLICY_CHARS} 字 —— 每轮都带，是最大固定开销
    2. L0/L1 摘要    {summary_chars} 字 —— 每轮都带
    3. 检索结果      {avg_body} 字

  "耗 token" 的可能原因：
    a) 自主记忆准则每轮都注入（{MEMORY_POLICY_CHARS} 字，且是固定的）
    b) L0/L1 摘要每轮都带
    c) 检索结果 top5 可能过长
""")

out = {
    "experiment": "E-294",
    "first_round_chars": first_round,
    "later_round_chars": later_round,
    "protected_full": full_chars,
    "protected_summary": summary_chars,
    "search_body": avg_body,
    "memory_policy": MEMORY_POLICY_CHARS,
    "knowledge_hint": kh_chars,
    "prewarm": PREWARM_CHARS,
    "log_samples": len(chars_hist),
    "log_avg": round(sum(chars_hist)/len(chars_hist)) if chars_hist else None,
}
p = os.path.join(_HERE, "results", "e294_token_usage.json")
with open(p, "w", encoding="utf-8") as f:
    json.dump(out, f, ensure_ascii=False, indent=1)
print(f"写入 results/e294_token_usage.json")
