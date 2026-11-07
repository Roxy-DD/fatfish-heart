"""
E-141 链式展开是否真的有效（第一次实测）

背景（读 docs/49 后发现）：
  用户定义「触发不是语义相似，是情景相似或情绪共振，而且会链式展开」
  code 里 store_v1._expand 已实现三条通路（情景相似/触发共现/情绪共振）
  但 search() 默认 expand=False，插件链路从未传 True
  → 这条能力**在当前系统里完全没生效**

本实验要回答：
  ① 开启 expand 后，能否召回"单跳搜不到但语义相关"的记忆？
  ② 代价是多少（延迟/多召回几条）？
  ③ 是否引入噪声（不该召回的也召回了）？

设计：
  构造"链式相关"样本：
    seed 是直接相关的记忆
    目标是通过 seed 才能到达的记忆（与 query 本身不相似，但与 seed 相关）
"""
from __future__ import annotations

import json
import os
import sys
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, "cosmos_mem"))
RESULTS = os.path.join(_HERE, "results")
DB = os.path.join(_HERE, "e141_expand.db")

# 链式结构：query → A（直接命中）→ B（经 A 才能到）
# 每组的 B 与 query 的字面/语义相似度都低
CASES = [
    {
        "query": "小时候的夏天",
        "A": "小时候夏天傍晚在院子里吃饭，天还很亮",
        "B": "外婆坐在竹椅上给我扇扇子，说天黑了就去睡觉",
        "desc": "情景链：夏天傍晚 → 外婆扇扇子",
    },
    {
        "query": "进不去门",
        "A": "家里的门锁密码是 3721",
        "B": "上个月忘带钥匙被锁在门外，在楼道坐了两个小时",
        "desc": "代价链：门锁 → 被锁门外",
    },
    {
        "query": "上次那个会议",
        "A": "上周三开了星火项目启动会",
        "B": "张伟在会上说接口文档下周交，结果拖了三天",
        "desc": "事件链：启动会 → 会上的承诺",
    },
    {
        "query": "我爸住院那阵",
        "A": "我爸去年冬天做了心脏支架手术",
        "B": "那段时间我每天下班先去医院再回家，瘦了十斤",
        "desc": "情绪链：住院 → 当时的疲惫",
    },
]

# 干扰记忆（与任何 query 和 A/B 都无关）
DISTRACTORS = [
    "缓存层用的是Redis 7", "消息队列用的是Kafka",
    "前端框架用React 18", "后端框架用FastAPI",
    "监控用Prometheus", "日志用ELK",
    "代码仓库托管在GitLab", "持续集成用Jenkins",
    "数据库用的是PostgreSQL 15", "服务器部署在阿里云华东区",
]


def main():
    from store_v2 import MemoryStore
    from store_v1 import Embedder
    import numpy as np

    for suf in ("", "-wal", "-shm"):
        p = DB + suf
        if os.path.exists(p):
            os.remove(p)

    st = MemoryStore(DB)
    emb = Embedder.get()

    # 写入全部记忆
    ids = {}
    for d in DISTRACTORS:
        st.add(d, resolve_conflict=False)
    for c in CASES:
        ids[c["A"]] = st.add(c["A"], resolve_conflict=False).ep_id
        ids[c["B"]] = st.add(c["B"], resolve_conflict=False).ep_id

    print("=" * 92)
    print("E-141 链式展开实测（expand=False vs True）")
    print("=" * 92)
    print(f"\n库中 {st.count()} 条记忆（{len(CASES)}×2 链式样本 + {len(DISTRACTORS)} 干扰）\n")

    print(f"{'查询':<18}{'模式':<12}{'召回条数':<10}{'B 是否召回':<12}{'耗时'}")
    print("-" * 92)

    results = []
    for c in CASES:
        b_id = ids[c["B"]]
        row = {"query": c["query"], "desc": c["desc"]}

        for mode, use_expand in [("单跳", False), ("链式", True)]:
            t0 = time.perf_counter()
            res = st.search(c["query"], top=5, expand=use_expand, hops=2)
            dt = (time.perf_counter() - t0) * 1000

            got_ids = [ep.ep_id for ep, *_ in res]
            has_b = b_id in got_ids
            b_rank = (got_ids.index(b_id) + 1) if has_b else 0

            print(f"{c['query'][:16]:<18}{mode:<12}{len(res):<10}"
                  f"{('是 #' + str(b_rank)) if has_b else '否':<12}{dt:.1f}ms")

            row[mode] = {"n": len(res), "has_b": has_b,
                         "b_rank": b_rank, "ms": round(dt, 1)}

        row["gain"] = (not row["单跳"]["has_b"]) and row["链式"]["has_b"]
        results.append(row)
        print()

    # 汇总
    print("=" * 92)
    print("汇总")
    print("=" * 92)

    n_gain = sum(1 for r in results if r["gain"])
    n_both = sum(1 for r in results
                 if r["单跳"]["has_b"] and r["链式"]["has_b"])
    n_none = sum(1 for r in results
                 if not r["单跳"]["has_b"] and not r["链式"]["has_b"])

    print(f"\n  B 单跳就召回（无需展开）: {n_both}/{len(results)}")
    print(f"  B 仅链式召回（展开有效）: {n_gain}/{len(results)}")
    print(f"  B 两者都没召回:          {n_none}/{len(results)}")

    avg_1hop = sum(r["单跳"]["ms"] for r in results) / len(results)
    avg_chain = sum(r["链式"]["ms"] for r in results) / len(results)
    print(f"\n  平均耗时：单跳 {avg_1hop:.1f}ms  链式 {avg_chain:.1f}ms"
          f"  （增加 {avg_chain - avg_1hop:+.1f}ms）")

    print(f"\n  逐条：")
    for r in results:
        mark = "✅ 展开有效" if r["gain"] else (
            "— 单跳已够" if r["单跳"]["has_b"] else "❌ 都失败")
        print(f"    {mark:<14}{r['desc']}")

    out = {"experiment": "E-141", "n": len(results),
           "gain": n_gain, "already": n_both, "miss": n_none,
           "avg_1hop_ms": round(avg_1hop, 1),
           "avg_chain_ms": round(avg_chain, 1),
           "details": results}
    with open(os.path.join(RESULTS, "e141_expand.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    st.con.close()
    print(f"\n写入 {os.path.join(RESULTS, 'e141_expand.json')}")


if __name__ == "__main__":
    main()
