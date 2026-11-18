"""验证单条截断不伤检索质量（E-534 后续）

════════════════════════════════════════════════════════════
要回答的问题
════════════════════════════════════════════════════════════
E-534 加了「单条截断到 300 字」。
省 token 是确定的（实测长尾查询省 50%），但**会不会伤检索质量**？

判断标准：截断前后的**召回集**是否一致。
· 如果 top-5 的 ep_id 完全相同 → 没伤质量（只是文字被截短）
· 如果 ep_id 变了 → 伤了（有些记忆因为截断被挤出去了）

注意：截断只影响「注入到上下文的文字长度」，
不影响「检索本身返回哪些 ep_id」。
所以理论上应该完全一致 —— 但要实测确认，
因为我的实现里有「预算用尽时二次截断」，可能改变保留集合。

════════════════════════════════════════════════════════════
测什么
════════════════════════════════════════════════════════════
1. 多组真实查询，对比 perItemMax=0（旧）vs 300（新）
   · 返回的 ep_id 集合是否一致
   · 注入字数差多少
2. 边界：预算很小时（200字），会不会一条都进不来
"""
import json
import sys
import warnings

warnings.filterwarnings("ignore")
sys.path.insert(0, "cosmos_mem")

from store_v2 import MemoryStore

st = MemoryStore("cosmos_mem/dsh_mem.db")


# ════════════════════════════════════════════════════════════
# 复刻插件的 formatMemories（Python 版），保证测的是同一逻辑
# ════════════════════════════════════════════════════════════
def format_memories(items, max_chars, min_score=0, per_item_max=0):
    if not items:
        return {"lines": [], "kept": 0, "truncated": 0, "chars": 0}
    lines = []
    total = 0
    skipped = 0
    truncated = 0

    for it in items:
        sc = it.get("score")
        if min_score > 0 and isinstance(sc, (int, float)) and sc < min_score:
            skipped += 1
            continue

        text = it.get("text") or ""
        if per_item_max > 0 and len(text) > per_item_max:
            text = text[:per_item_max] + "…（已截断，需要全文请 mem_search）"
            truncated += 1

        note = it.get("note")
        line = f"- [{it['ep_id']}] {text}" + (f"  ({note})" if note else "")

        if total + len(line) > max_chars:
            room = max_chars - total
            if per_item_max > 0 and room > 40 and len(line) > room:
                keep = max(20, room - 40)
                clipped = f"- [{it['ep_id']}] {text[:keep]}…（截断）"
                if total + len(clipped) <= max_chars:
                    lines.append(clipped)
                    total += len(clipped)
                    truncated += 1
                    continue
            break

        lines.append(line)
        total += len(line)

    return {"lines": lines, "kept": len(lines), "truncated": truncated,
            "chars": total}


print("=" * 72)
print("E-534 验证：截断是否伤检索质量")
print("=" * 72)

# ---------- 测试查询 ----------
QUERIES = [
    "记忆系统怎么优化",
    "token 开销优化",
    "继续完善记忆系统",
    "itsaplan 怎么部署",
    "红线是什么",
    "Podman 端口转发",
    "为什么登录失败",
    "异地副本怎么恢复",
]

BUDGET = 1500

results = []
print(f"\n{'查询':<24} {'旧:条/字':>12} {'新:条/字':>12} {'ep_id一致':>10} {'省':>6}")
print("-" * 72)

for q in QUERIES:
    hits = st.search(q, top=5)

    items = []
    for h in hits:
        ep = h[0] if isinstance(h, tuple) else h
        sc = h[1] if isinstance(h, tuple) and len(h) > 1 else None
        flag = h[2] if isinstance(h, tuple) and len(h) > 2 else 0
        # ★ 关键：插件在 L3042 用 filter(it => it.flag !== 2) 排除 L0/L1
        #   L0/L1 走独立的 protected 通道，不占 body 预算。
        #   我的第一版测试没做这个过滤，导致把 score=0 的 L0/L1
        #   也算进了 body，测出的"注入变多"是假的。
        if flag == 2:
            continue
        items.append({
            "ep_id": getattr(ep, "ep_id", "?"),
            "text": getattr(ep, "raw_text", "") or "",
            "score": sc if isinstance(sc, (int, float)) else None,
            "note": None,
        })

    old = format_memories(items, BUDGET, 0, 0)     # 不截断
    new = format_memories(items, BUDGET, 0, 300)   # 截断 300

    def ids(r):
        return sorted(
            ln.split("]")[0].replace("- [", "") for ln in r["lines"]
        )

    same = ids(old) == ids(new)
    saved = old["chars"] - new["chars"]
    saved_pct = (saved / old["chars"] * 100) if old["chars"] else 0

    print(f"{q:<24} {old['kept']:>2}条/{old['chars']:>5}字 "
          f"{new['kept']:>2}条/{new['chars']:>5}字 "
          f"{'✅' if same else '❌ 变了':>8} {saved_pct:>5.0f}%")

    results.append({
        "query": q, "same_ids": same,
        "old_kept": old["kept"], "new_kept": new["kept"],
        "old_chars": old["chars"], "new_chars": new["chars"],
    })

# ---------- 汇总 ----------
print("\n" + "=" * 72)
n_same = sum(1 for r in results if r["same_ids"])
total_old = sum(r["old_chars"] for r in results)
total_new = sum(r["new_chars"] for r in results)
saved_all = total_old - total_new

print(f"ep_id 集合一致: {n_same}/{len(results)}")
print(f"总注入: {total_old:,} 字 → {total_new:,} 字  "
      f"（省 {saved_all:,} 字, {saved_all / total_old * 100:.1f}%）")

# ---------- 边界测试 ----------
print("\n【边界】小预算下会不会一条都进不来")
q = QUERIES[0]
hits = st.search(q, top=5)
items = [{
    "ep_id": getattr(h[0], "ep_id", "?"),
    "text": getattr(h[0], "raw_text", "") or "",
    "score": h[1] if len(h) > 1 else None,
    "note": None,
} for h in hits]
for budget in (100, 200, 400):
    o = format_memories(items, budget, 0, 0)
    n = format_memories(items, budget, 0, 300)
    print(f"  budget={budget:4d}  旧={o['kept']}条/{o['chars']}字  "
          f"新={n['kept']}条/{n['chars']}字")

st.con.close()

# ---------- 写结果 ----------
out = {
    "experiment": "E-534-verify",
    "budget": BUDGET,
    "per_item_max": 300,
    "query_count": len(QUERIES),
    "same_ids_count": n_same,
    "total_old_chars": total_old,
    "total_new_chars": total_new,
    "saved_chars": saved_all,
    "saved_pct": round(saved_all / total_old * 100, 1) if total_old else 0,
    "results": results,
}
p = "results/e534_verify_truncation.json"
with open(p, "w", encoding="utf-8") as f:
    json.dump(out, f, ensure_ascii=False, indent=1)
print(f"\n写入 {p}")

print("\n" + "=" * 72)
print("结论")
print("=" * 72)
if n_same == len(results):
    print("✅ 所有查询的 ep_id 集合完全一致 —— 截断只缩短文字，不改变召回")
    print(f"✅ 平均省 {saved_all / total_old * 100:.1f}% 字符")
else:
    print(f"⚠ {len(results) - n_same} 个查询的召回集变了，需要检查")
    for r in results:
        if not r["same_ids"]:
            print(f"   · {r['query']}: {r['old_kept']}条 → {r['new_kept']}条")
