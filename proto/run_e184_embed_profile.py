"""
E-184 当前 embedding 的纯语义上限（换模型前的基线）

E-183 已测：bge-small-zh 纯语义 recall@10 = 0.5466

但那是"每题最好的 evidence"的排名。
本实验测得更细：
  ① 每个类别的纯语义上限（看哪类最差）
  ② 查询与证据的余弦**绝对值**分布（低余弦说明语义真的不匹配）
  ③ 与 LoCoMo 论文/Mem0 报告的对比参考点

目的：确定"换 embedding"的预期收益上限。
  如果当前纯语义已 0.55，而 SOTA embedding 可能到 0.75，
  那换模型的收益空间约 20pp，值得做。

不修改任何生产代码。
"""
from __future__ import annotations

import json
import os
import re
import sys
from collections import defaultdict

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, "cosmos_mem"))
TASK = os.path.join(_HERE, "work", "e184")
os.makedirs(TASK, exist_ok=True)
RESULTS = os.path.join(_HERE, "results")
DATA = os.path.join(_HERE, "work", "e165", "locomo10.json")

K = 10
N_CONV = 3
CAT = {"1": "单跳", "2": "时间", "3": "多跳", "4": "常识", "5": "对抗"}


def main():
    from store_v2 import MemoryStore
    from store_v1 import Embedder
    import numpy as np

    data = json.load(open(DATA, encoding="utf-8"))
    emb = Embedder.get()

    print("=" * 94)
    print("E-184 当前 embedding 的纯语义能力画像")
    print("=" * 94)
    print(f"\n模型: bge-small-zh（512 维）")
    print(f"数据: LoCoMo 前 {N_CONV} 个对话\n")

    by_cat = defaultdict(lambda: {"n": 0, "hit10": 0, "hit30": 0,
                                  "sims": []})
    all_sims = []

    for conv in data[:N_CONV]:
        sid = conv.get("sample_id")
        qa = conv.get("qa") or []
        c = conv.get("conversation") or {}

        sessions = []
        j = 1
        while f"session_{j}" in c:
            if c[f"session_{j}"]:
                sessions.append((j, c.get(f"session_{j}_date_time", ""),
                                 c[f"session_{j}"]))
            j += 1

        db = os.path.join(TASK, f"{sid}.db")
        for suf in ("", "-wal", "-shm"):
            p = db + suf
            if os.path.exists(p):
                try:
                    os.remove(p)
                except Exception:
                    pass
        st = MemoryStore(db)

        id_of_mark = {}
        for snum, dt, turns in sessions:
            for ti, turn in enumerate(turns, start=1):
                spk = turn.get("speaker") or ""
                txt = turn.get("text") or turn.get("content") or ""
                txt = re.sub(r"\s+", " ", str(txt)).strip()
                if len(txt) < 5:
                    continue
                full = f"[{dt}] {spk}: {txt}" if dt else f"{spk}: {txt}"
                try:
                    ep = st.add(full[:800], resolve_conflict=False)
                    id_of_mark[f"D{snum}:{ti}"] = ep.ep_id
                except Exception:
                    pass

        st._load_vectors(include_invalid=False)
        norms = np.linalg.norm(st._vec, axis=1)
        norms[norms == 0] = 1.0
        idx_of_id = {eid: i for i, eid in enumerate(st._vec_ids)}

        for q in qa:
            question = q.get("question") or ""
            cat = str(q.get("category"))
            ev = q.get("evidence") or []
            if not isinstance(ev, list):
                ev = [ev]
            ev = [str(e).strip() for e in ev if e]
            if not question or not ev:
                continue

            nv = emb.encode(question)[0]
            sims = (st._vec @ nv) / norms

            best_rank = None
            best_sim = None
            for e in ev:
                eid = id_of_mark.get(e)
                if not eid or eid not in idx_of_id:
                    continue
                i = idx_of_id[eid]
                r = int((sims > sims[i]).sum()) + 1
                s = float(sims[i])
                if best_rank is None or r < best_rank:
                    best_rank, best_sim = r, s

            if best_rank is None:
                continue

            d = by_cat[cat]
            d["n"] += 1
            d["hit10"] += 1 if best_rank <= 10 else 0
            d["hit30"] += 1 if best_rank <= 30 else 0
            d["sims"].append(best_sim)
            all_sims.append(best_sim)

        st.con.close()

    # ---------- 汇总 ----------
    print("=" * 94)
    print("① 各类别的纯语义上限")
    print("=" * 94)
    print(f"\n{'类别':<10}{'题数':<8}{'recall@10':<14}{'recall@30':<14}"
          f"{'平均余弦'}")
    print("-" * 94)

    tot_n = tot_10 = tot_30 = 0
    for c in sorted(by_cat):
        d = by_cat[c]
        n = d["n"]
        r10 = d["hit10"] / n if n else 0
        r30 = d["hit30"] / n if n else 0
        avg = sum(d["sims"]) / n if n else 0
        print(f"{CAT.get(c, c):<10}{n:<8}{r10:<14.4f}{r30:<14.4f}{avg:.4f}")
        tot_n += n
        tot_10 += d["hit10"]
        tot_30 += d["hit30"]

    print("-" * 94)
    print(f"{'总体':<10}{tot_n:<8}{tot_10/tot_n:<14.4f}"
          f"{tot_30/tot_n:<14.4f}{sum(all_sims)/len(all_sims):.4f}")

    # ---------- 余弦分布 ----------
    print("\n" + "=" * 94)
    print("② 证据与问题的余弦绝对值分布")
    print("=" * 94)

    import statistics
    buckets = {"<0.3": 0, "0.3-0.4": 0, "0.4-0.5": 0,
               "0.5-0.6": 0, "0.6-0.7": 0, ">=0.7": 0}
    for s in all_sims:
        if s < 0.3:
            buckets["<0.3"] += 1
        elif s < 0.4:
            buckets["0.3-0.4"] += 1
        elif s < 0.5:
            buckets["0.4-0.5"] += 1
        elif s < 0.6:
            buckets["0.5-0.6"] += 1
        elif s < 0.7:
            buckets["0.6-0.7"] += 1
        else:
            buckets[">=0.7"] += 1

    n = len(all_sims)
    print(f"\n{'余弦区间':<14}{'条数':<10}{'占比'}")
    print("-" * 40)
    for b in ["<0.3", "0.3-0.4", "0.4-0.5", "0.5-0.6", "0.6-0.7", ">=0.7"]:
        c = buckets[b]
        print(f"{b:<14}{c:<10}{c/n:.1%}")

    print(f"\n  中位数: {statistics.median(all_sims):.4f}")
    print(f"  均值:   {statistics.mean(all_sims):.4f}")

    # ---------- 结论 ----------
    print("\n" + "=" * 94)
    print("结论（换模型的预期收益）")
    print("=" * 94)

    low_cos = (buckets["<0.3"] + buckets["0.3-0.4"]) / n
    print(f"\n  当前纯语义 recall@10 = {tot_10/tot_n:.4f}")
    print(f"  余弦 <0.4 的占比 = {low_cos:.1%}（这些题语义真的不匹配）")

    print(f"\n  参考：SOTA embedding (Qwen3-0.6B) 在 MMTEB 上 64.34 分，")
    print(f"        当前 bge-small-zh 未上榜（榜单最低约 60 分）")
    print(f"        但 MMTEB 分数不能直接换算成 LoCoMo recall")

    print(f"\n  判断：")
    if low_cos > 0.3:
        print(f"    → {low_cos:.0%} 的证据余弦 <0.4，说明向量空间里确实不像")
        print(f"    → 换更强模型有明确空间（预期 10-20pp）")
    else:
        print(f"    → 大部分证据余弦 >0.4，说明语义已匹配但排序不够")
        print(f"    → 换模型收益有限，应优化排序")

    out = {"experiment": "E-184",
           "by_cat": {c: {"n": d["n"],
                          "recall10": round(d["hit10"] / d["n"], 4),
                          "recall30": round(d["hit30"] / d["n"], 4),
                          "avg_sim": round(sum(d["sims"]) / d["n"], 4)}
                      for c, d in by_cat.items()},
           "overall_recall10": round(tot_10 / tot_n, 4),
           "overall_recall30": round(tot_30 / tot_n, 4),
           "sim_buckets": buckets,
           "sim_median": round(statistics.median(all_sims), 4),
           "low_cos_ratio": round(low_cos, 4)}
    with open(os.path.join(RESULTS, "e184_embed_profile.json"), "w",
              encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(f"\n写入 {os.path.join(RESULTS, 'e184_embed_profile.json')}")


if __name__ == "__main__":
    main()
