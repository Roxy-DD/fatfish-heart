"""
E-132 迭代扩散检索：小 top_k 起步 + 模型决定是否扩散

用户设计（2026-11-02）：
  "优先用余弦直接快速找到，就像我们用查找工具直接在文档里面查一样。
   当我们看到的结果不一样的时候，再让模型自主地去确定要不要扩散、
   要不要提高这个 top k，然后去查找别的地方，而不是上来就给一个确定的 top k。"

E-131 暴露的问题：
  固定 top_k 在任何规模下都会失效：
    1010 条 → 需 k=10
    5010 条 → 需 k=50
    10010 条 → 需 k=100

本实验验证迭代扩散方案：
  第1轮：top_k=10（快速）
  模型判断：这些候选里有目标吗？
    · 有     → 停止，判关系
    · 没有   → 但相似度仍然很高（说明"像"但找不到具体哪条）
              → 扩散：k=20, 40, 80, 160 ...
  直到命中 或 达到上限

两个关键指标：
  ① 命中率（最终能否找到目标）
  ② 扩散成本（平均轮数、平均总候选条数）

对比基线：固定 top_k=100（每次都给 100 条）
"""
from __future__ import annotations

import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, "cosmos_mem"))
TASK = os.path.join(_HERE, "work", "e131")
RESULTS = os.path.join(_HERE, "results")

TARGETS = [
    ("我的车是白色卡罗拉", "我换车了现在是黑色大众"),
    ("我住在城东花园3栋", "我搬家到城西新村5号"),
    ("家里的门锁密码是 3721", "门锁密码换成了9512"),
    ("接口参数定的是0.85", "接口参数改成了1.2"),
    ("这个项目 15 号截止", "客户同意把截止延到20号"),
    ("明天下午三点开会", "会议改到明天上午十点"),
    ("我电脑的登录密码是 8848", "我把电脑密码改成了9988"),
    ("客户要求界面必须支持深色模式", "客户还要求界面必须支持大字体"),
    ("我要连续吃两周的药", "另外我晚上也要吃一次药"),
    ("老王说门锁密码是 3721", "小李说门锁密码是9512"),
]

# 扩散序列：指数增长
LADDER = [10, 20, 40, 80, 160]


def iterative_search(st, emb, new_text, target_text, ladder=LADDER,
                     sim_floor=0.5):
    """
    迭代扩散检索。

    每轮：
      取 top_k 候选
      判断目标是否在候选里（这一步在真实系统里由模型做：
        模型看到候选里没有"同一槽位的旧记忆"，会判 none，
        而系统预筛认为相似度高 → 这就是"该扩散"的信号）
      若不在，且最高相似度仍 ≥ sim_floor → 扩散到下一档
      若最高相似度已 < sim_floor → 说明确实无关，停止（不浪费）

    返回：轮次记录 + 最终是否命中
    """
    import numpy as np

    st._load_vectors(include_invalid=False)
    norms = np.linalg.norm(st._vec, axis=1)
    norms[norms == 0] = 1.0
    nv = emb.encode(new_text)[0]
    nv_norm = float(np.linalg.norm(nv)) or 1.0
    sims = (st._vec @ nv) / (norms * nv_norm)

    rows = st.con.execute(
        "SELECT ep_id, raw_text FROM episodes WHERE invalid_at IS NULL").fetchall()
    text_of = {e: t for e, t in rows}
    id_of_text = {t: e for e, t in text_of.items()}
    tgt_id = id_of_text.get(target_text)

    rounds = []
    total_cands = 0
    hit = False
    hit_rank = 0

    for k in ladder:
        k_eff = min(k, len(sims))
        idx = np.argpartition(-sims, k_eff - 1)[:k_eff]
        idx = idx[np.argsort(-sims[idx])]
        cands = [(st._vec_ids[int(i)], text_of.get(st._vec_ids[int(i)], ""),
                  float(sims[int(i)])) for i in idx]
        total_cands += len(cands)

        top_cos = cands[0][2] if cands else 0.0
        found = any(c[0] == tgt_id for c in cands)
        rank = next((i + 1 for i, c in enumerate(cands) if c[0] == tgt_id), 0)

        # 系统判断是否值得继续扩散：
        # 若最高相似度已低于 floor，说明真的无关，扩散无用
        can_expand = top_cos >= sim_floor

        rounds.append({"k": k, "n_cands": len(cands),
                       "found": found, "rank": rank,
                       "top_cos": round(top_cos, 4),
                       "would_expand": (not found) and can_expand})

        if found:
            hit, hit_rank = True, rank
            break
        if not can_expand:
            break

    return {"rounds": rounds, "hit": hit, "hit_rank": hit_rank,
            "n_rounds": len(rounds), "total_cands": total_cands}


def main():
    from store_v2 import MemoryStore
    from store_v1 import Embedder

    print("=" * 94)
    print("E-132 迭代扩散检索 vs 固定 top_k")
    print("=" * 94)
    print("\n扩散序列:", LADDER)

    all_res = {}

    for n_dist in [1010, 5010, 10010]:
        db = os.path.join(TASK, f"e131_{n_dist - 10}.db")
        if not os.path.exists(db):
            print(f"\n跳过 {n_dist}（缺库 {db}）")
            continue

        st = MemoryStore(db)
        emb = Embedder.get()

        print(f"\n{'=' * 94}")
        print(f"库规模 {n_dist}")
        print(f"{'=' * 94}")
        print(f"\n{'用例':<30}{'轮数':<8}{'总候选':<10}"
              f"{'命中':<8}{'排名':<8}{'每轮k'}")
        print("-" * 94)

        rows_out = []
        for tgt, new in TARGETS:
            r = iterative_search(st, emb, new, tgt)
            ks = ",".join(str(x["k"]) for x in r["rounds"])
            print(f"{new[:28]:<30}{r['n_rounds']:<8}{r['total_cands']:<10}"
                  f"{'✓' if r['hit'] else '✗':<8}{r['hit_rank'] or '-':<8}{ks}")
            rows_out.append({"new": new, "target": tgt, **r})

        st.con.close()

        n_hit = sum(1 for r in rows_out if r["hit"])
        avg_rounds = sum(r["n_rounds"] for r in rows_out) / len(rows_out)
        avg_cands = sum(r["total_cands"] for r in rows_out) / len(rows_out)
        # 对比：固定 top_k=100 每次都给 100 条
        fixed_cost = 100

        print("-" * 94)
        print(f"  命中率: {n_hit}/{len(rows_out)} = {n_hit/len(rows_out):.4f}")
        print(f"  平均轮数: {avg_rounds:.2f}")
        print(f"  平均总候选条数: {avg_cands:.1f}")
        print(f"  固定 top_k=100 成本: {fixed_cost} 条/条记忆")
        print(f"  节省: {(1 - avg_cands / fixed_cost) * 100:.1f}%")

        all_res[str(n_dist)] = {
            "hit": n_hit, "total": len(rows_out),
            "hit_rate": round(n_hit / len(rows_out), 4),
            "avg_rounds": round(avg_rounds, 2),
            "avg_cands": round(avg_cands, 1),
            "fixed_cost": fixed_cost,
            "saving_pct": round((1 - avg_cands / fixed_cost) * 100, 1),
            "details": rows_out,
        }

    # ---------- 汇总 ----------
    print("\n" + "=" * 94)
    print("汇总")
    print("=" * 94)
    print(f"{'规模':<10}{'命中率':<14}{'平均轮数':<12}{'平均候选':<12}{'vs固定100'}")
    print("-" * 94)
    for scale, r in all_res.items():
        print(f"{scale:<10}{r['hit_rate']:<14}{r['avg_rounds']:<12}"
              f"{r['avg_cands']:<12}-{r['saving_pct']}%")

    # ---------- 结论 ----------
    print("\n" + "=" * 94)
    print("结论")
    print("=" * 94)
    all_hit_full = all(r["hit_rate"] == 1.0 for r in all_res.values())
    if all_hit_full:
        print("  → ✅ 所有规模下命中率 100%（固定 top_k 做不到这一点）")
    else:
        print("  → ⚠️ 仍有未命中，需扩扩散上限")

    for scale, r in all_res.items():
        print(f"  规模 {scale}: 平均 {r['avg_rounds']} 轮、"
              f"{r['avg_cands']} 条候选，比固定 100 省 {r['saving_pct']}%")

    out = {"experiment": "E-132", "ladder": LADDER,
           "results": all_res}
    with open(os.path.join(RESULTS, "e132_iterative.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(f"\n写入 {os.path.join(RESULTS, 'e132_iterative.json')}")


if __name__ == "__main__":
    main()
