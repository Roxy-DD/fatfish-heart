"""E-349 验证 bug① 修复：首轮是否真的给全文？

E-348 修了 formatProtected(protectedItems, !st.injected) 的顺序错误。
修复后首轮应该：
  · formatProtected 的 full=true → 给出 L0/L1 全文
  · 提示语为「以下是系统长期准则，请在后续所有回答中遵循」

验证方法：
  ① 从日志找注入记录，看 content_chars
  ② 首轮全文 ~2856 字 vs 摘要 ~570 字，差异明显
  ③ 检查 audit_log 里 inject 的 content_chars 分布
"""
import sys
import os
import json
import time
import warnings
import sqlite3

warnings.filterwarnings("ignore")
_HERE = os.path.dirname(os.path.abspath(__file__))
MEM = os.path.join(_HERE, "cosmos_mem")
DB = os.path.join(MEM, "dsh_mem.db")

print("=" * 76)
print("E-349 验证首轮全文是否生效")
print("=" * 76)

con = sqlite3.connect(DB)
rows = con.execute(
    "SELECT seq, at, sid, msg, extra FROM audit_log "
    "WHERE kind='inject' ORDER BY at").fetchall()
con.close()

print(f"\n① inject 记录: {len(rows)} 条")

# ---------- ② 解析 content_chars ----------
print("\n② content_chars 分布（首轮全文 vs 摘要）")

data = []
for seq, at, sid, msg, extra in rows:
    ex = {}
    try:
        ex = json.loads(extra or "{}")
    except Exception:
        pass
    cc = ex.get("content_chars")
    pc = ex.get("protected_count")
    q = str(ex.get("query") or "")[:35]
    data.append({"seq": seq, "at": at, "sid": sid,
                 "content_chars": cc, "protected_count": pc, "query": q})

with_cc = [d for d in data if isinstance(d["content_chars"], int)]
print(f"   有 content_chars 的: {len(with_cc)}/{len(data)}")

if with_cc:
    ccs = sorted(d["content_chars"] for d in with_cc)
    print(f"   范围: {ccs[0]} ~ {ccs[-1]}")
    print(f"   中位数: {ccs[len(ccs)//2]}")
    print(f"\n   最近 10 次:")
    for d in with_cc[-10:]:
        t = time.strftime("%m-%d %H:%M", time.localtime(d["at"])) if d["at"] else "?"
        print(f"     {t}  chars={d['content_chars']:>6}  "
              f"protected={d['protected_count']}  '{d['query'][:30]}'")

    # 关键：区分"全文"和"摘要"
    # 全文 ~2856 字（11 条 L0/L1），摘要 ~570 字
    full_count = sum(1 for d in with_cc if d["content_chars"] > 1500)
    sum_count = sum(1 for d in with_cc if d["content_chars"] <= 1500)
    print(f"\n   >1500 字（疑似全文）: {full_count}")
    print(f"   <=1500 字（疑似摘要）: {sum_count}")

# ---------- ③ 检查 L0/L1 全文实际长度 ----------
print("\n③ L0/L1 全文的实际长度")
sys.path.insert(0, MEM)
try:
    import hook as _hook
    from store_v2 import MemoryStore
    st = MemoryStore(DB)
    prot = _hook.protected_of(st)
    total = sum(len(p.get("text") or "") for p in prot)
    print(f"   受保护记忆: {len(prot)} 条")
    print(f"   全文总长: {total} 字")
    print(f"   若给标题（每条约 8-15 字）: ~{len(prot)*12} 字")
    print(f"\n   预期：")
    print(f"     首轮全文：{total} + 检索结果 ~500 + 准则 877 ≈ {total+1400} 字")
    print(f"     后续摘要：~{len(prot)*12} + 检索结果 + 一行提醒 ≈ 600 字")
    st.con.close()
except Exception as e:
    print(f"   读取失败: {e}")

# ---------- ④ 结论 ----------
print("\n" + "=" * 76)
print("结论")
print("=" * 76)

if not with_cc:
    print("""
  ⚠ 没有 content_chars 数据

  说明 inject_log 的 extra 里没记这个字段，
  或者记录的时间在我加这个字段之前。
  → 无法从历史数据验证，需要等新会话产生数据。
""")
else:
    has_full = any(d["content_chars"] > 1500 for d in with_cc)
    print(f"""
  观测到 >1500 字的注入: {'✅ 有' if has_full else '❌ 无'}

  {'✅ 首轮全文生效' if has_full else '⚠ 还没观测到全文注入（可能需要新会话）'}
""")

out = {
    "experiment": "E-349",
    "n_inject": len(rows),
    "n_with_cc": len(with_cc),
}
p = os.path.join(_HERE, "results", "e349_fulltext.json")
with open(p, "w", encoding="utf-8") as f:
    json.dump(out, f, ensure_ascii=False, indent=1)
print(f"写入 results/e349_fulltext.json")
