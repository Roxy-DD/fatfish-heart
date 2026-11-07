"""
E-130 top_k 规模实验：候选包要多大才能保住目标

E-129 显示三种排序策略都是 0.80，说明问题不在排序而在候选池容量。

本质原因：
  新记忆是"旧值+改动"，干扰库里有大量"同槽不同值"的记忆，
  它们与新记忆的字面相似度更高（因为它们和"新值"字面接近），
  天然占据 top-10。

  这**不是缺陷**，是真实的检索难题。正确的解法不是改排序，
  而是扩大候选池 —— 反正候选包给模型看，多几条成本极低
  （模型只读文本，不重算余弦）。

实测不同 top_k 下的命中率与模型输入成本。
"""
from __future__ import annotations

import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, "cosmos_mem"))
TASK = os.path.join(_HERE, "work", "e128")
RESULTS = os.path.join(_HERE, "results")

CASES = [
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

TOP_K_LIST = [10, 15, 20, 30, 50]


def main():
    from store_v2 import MemoryStore
    from store_v1 import Embedder
    import numpy as np

    db = os.path.join(TASK, "e128_1000.db")
    if not os.path.exists(db):
        print("缺少测试库")
        return

    st = MemoryStore(db)
    emb = Embedder.get()

    rows = st.con.execute(
        "SELECT ep_id, raw_text FROM episodes WHERE invalid_at IS NULL").fetchall()
    text_of = {e: t for e, t in rows}
    id_of_text = {t: e for e, t in text_of.items()}

    st._load_vectors(include_invalid=False)
    norms = np.linalg.norm(st._vec, axis=1)
    norms[norms == 0] = 1.0

    print("=" * 94)
    print("E-130 top_k 规模实验（1000 条库）")
    print("=" * 94)

    # 先算每条用例的全量排名
    ranks = {}
    for target, new in CASES:
        tgt_id = id_of_text.get(target)
        if not tgt_id:
            continue
        nv = emb.encode(new)[0]
        nv_norm = float(np.linalg.norm(nv)) or 1.0
        sims = (st._vec @ nv) / (norms * nv_norm)
        tgt_idx = st._vec_ids.index(tgt_id)
        order = np.argsort(-sims)
        rank = int(np.where(order == tgt_idx)[0][0]) + 1
        ranks[new] = rank

    print(f"\n{'用例':<32}{'目标全量排名'}")
    print("-" * 94)
    for target, new in CASES:
        if new in ranks:
            print(f"{new[:30]:<32}#{ranks[new]}")

    max_rank = max(ranks.values())
    print(f"\n目标最差排名: #{max_rank}")

    # 各 top_k 下的命中率
    print("\n" + "-" * 94)
    print("不同 top_k 的命中率")
    print("-" * 94)
    print(f"{'top_k':<10}{'命中率':<14}{'覆盖?':<10}{'候选包字符数(估)'}")
    print("-" * 94)

    table = []
    for k in TOP_K_LIST:
        hit = sum(1 for _, n in CASES if ranks.get(n, 9999) <= k)
        rate = hit / len(CASES)
        covers = k >= max_rank
        # 估算候选包字符数：平均每条 77 字 + JSON 结构约 40
        est_chars = k * (77 + 40)
        table.append({"top_k": k, "hit": hit, "rate": rate,
                      "covers_worst": covers, "est_chars": est_chars})
        print(f"{k:<10}{hit}/{len(CASES)} = {rate:.2f}  "
              f"{'✅' if covers else '✗':<10}{est_chars}")

    st.con.close()

    # ---------- 结论 ----------
    print("\n" + "=" * 94)
    print("结论")
    print("=" * 94)
    ok = [t for t in table if t["rate"] == 1.0]
    if ok:
        best = ok[0]
        print(f"  top_k={best['top_k']} 时命中率 100%")
        print(f"  候选包约 {best['est_chars']} 字符 ≈ "
              f"{est_chars_to_tokens(best['est_chars'])} tokens")
        print(f"  模型判断输入成本：可接受（单次判断的 prompt 增量）")
    else:
        print(f"  即使 top_k={TOP_K_LIST[-1]} 也无法覆盖全部目标")
        print(f"  最差排名 #{max_rank}，需要更大候选池或改策略")

    print(f"\n  对比：top_k=10 命中 0.80 → top_k={ok[0]['top_k'] if ok else 'N/A'}"
          f" 命中 1.00")

    out = {"experiment": "E-130", "max_rank": max_rank,
           "table": table, "ranks": ranks}
    with open(os.path.join(RESULTS, "e130_topk.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(f"\n写入 {os.path.join(RESULTS, 'e130_topk.json')}")


def est_chars_to_tokens(chars):
    """粗估 token（中文约 1 字 ≈ 1.5 token）"""
    return int(chars * 1.5)


if __name__ == "__main__":
    main()
