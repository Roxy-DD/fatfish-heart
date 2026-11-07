"""
E-136 重复记忆量化：库里有大量近义重复

E-135 扫描发现：
  131 条"需要判定"的记忆，绝大多数相似度 0.95-0.99，
  内容几乎相同（如"用户要求每次新会话开始时先查看记忆"被记了 4 次）。

这比矛盾更严重：重复会挤占检索席位、浪费存储、拉低多样性。

本实验量化：
  ① 有多少条是重复的（按余弦聚类）
  ② 重复占了多少比例
  ③ 去重后能释放多少空间
"""
from __future__ import annotations

import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, "cosmos_mem"))
RESULTS = os.path.join(_HERE, "results")
DB = os.path.join(_HERE, "cosmos_mem", "dsh_mem.db")


def main():
    from store_v2 import MemoryStore
    from store_v1 import Embedder
    import numpy as np

    st = MemoryStore(DB)
    emb = Embedder.get()

    rows = st.con.execute(
        "SELECT ep_id, raw_text FROM episodes WHERE invalid_at IS NULL").fetchall()
    n = len(rows)
    print("=" * 88)
    print("E-136 重复记忆量化")
    print("=" * 88)
    print(f"\n有效记忆: {n} 条")

    st._load_vectors(include_invalid=False)
    norms = np.linalg.norm(st._vec, axis=1)
    norms[norms == 0] = 1.0
    V = st._vec / norms[:, None]

    # 计算两两余弦（n=136 很小，直接全算）
    S = V @ V.T
    text_of = {e: t for e, t in rows}

    # 以 0.95 为重复门槛做聚类（贪心：按顺序归到第一个相似的簇）
    THRESHOLDS = [0.90, 0.93, 0.95, 0.97]
    print(f"\n不同门槛下的重复率:")
    print(f"{'门槛':<10}{'簇数':<10}{'重复条数':<12}{'重复率'}")
    print("-" * 88)

    tables = {}
    for th in THRESHOLDS:
        assigned = [-1] * n
        clusters = []
        for i in range(n):
            if assigned[i] >= 0:
                continue
            cid = len(clusters)
            members = [i]
            assigned[i] = cid
            for j in range(i + 1, n):
                if assigned[j] >= 0:
                    continue
                if S[i, j] >= th:
                    assigned[j] = cid
                    members.append(j)
            clusters.append(members)
        dup = n - len(clusters)
        rate = dup / n
        tables[th] = {"clusters": len(clusters), "dup": dup, "rate": rate}
        print(f"{th:<10}{len(clusters):<10}{dup:<12}{rate:.1%}")

    # 用 0.95 详细看
    th = 0.95
    assigned = [-1] * n
    clusters = []
    for i in range(n):
        if assigned[i] >= 0:
            continue
        cid = len(clusters)
        members = [i]
        assigned[i] = cid
        for j in range(i + 1, n):
            if assigned[j] >= 0:
                continue
            if S[i, j] >= th:
                assigned[j] = cid
                members.append(j)
        clusters.append(members)

    multi = [c for c in clusters if len(c) > 1]
    print(f"\n" + "-" * 88)
    print(f"0.95 门槛下：{len(clusters)} 个簇，其中 {len(multi)} 个簇有多条")
    print(f"\n重复最严重的 10 个簇:")
    print("-" * 88)

    multi.sort(key=lambda c: -len(c))
    for c in multi[:10]:
        print(f"\n  【{len(c)} 条重复】")
        for idx in c:
            eid = rows[idx][0]
            print(f"    [{eid[:8]}] {text_of[eid][:64]}")

    # 存储估算
    total_bytes = os.path.getsize(DB)
    dup_rate = tables[th]["rate"]
    print(f"\n" + "=" * 88)
    print("结论")
    print("=" * 88)
    print(f"  库文件: {total_bytes/1024:.0f} KB")
    print(f"  0.95 门槛下重复率: {dup_rate:.1%}")
    print(f"  去重后约可释放: {total_bytes * dup_rate / 1024:.0f} KB")

    # 检索影响：重复会挤占席位
    print(f"\n  检索影响：如果某个槽位有 4 条重复，")
    print(f"  检索 top-10 里可能有 4 个席位都被它占用，")
    print(f"  实际只能看到 7 条不同的记忆。")

    out = {"experiment": "E-136", "n": n,
           "thresholds": {str(k): v for k, v in tables.items()},
           "db_bytes": total_bytes,
           "worst_clusters": [[rows[i][0] for i in c] for c in multi[:10]]}
    with open(os.path.join(RESULTS, "e136_duplicates.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)

    st.con.close()
    print(f"\n写入 {os.path.join(RESULTS, 'e136_duplicates.json')}")


if __name__ == "__main__":
    main()
