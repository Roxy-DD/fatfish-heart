"""
E-142 链式展开的真实价值（修正版）

E-141 失败原因：测试样本的 B 与 query 本身语义也相近 → 单跳就命中了，
测不出展开的价值。

正确设计：B 必须**与 query 语义无关**，只能经由 A 到达。
  例：
    query: "海边那次"          ← 直接语义指向 A
    A:     "去年夏天去了趟青岛海边"  ← 与 query 相似
    B:     "我们在栈桥吃了烤鱿鱼，那天下小雨"  ← 与 query 语义无关，但与 A 是同一情景

  单跳找 A 容易，找 B 难（B 里没有"海边"这个词）
  链式：命中 A 后，沿情景相似/情绪共振展开到 B
"""
from __future__ import annotations

import json
import os
import sys
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, "cosmos_mem"))
RESULTS = os.path.join(_HERE, "results")
DB = os.path.join(_HERE, "e142_expand.db")

# B 与 query 在字面和语义上都远，但通过 A 能连上
CASES = [
    {
        "query": "海边那次旅行",
        "A": "去年夏天我们去了青岛海边玩",
        "B": "在栈桥吃了烤鱿鱼，那天下着小雨",
        "desc": "海边 → 栈桥吃鱿鱼",
    },
    {
        "query": "我爸那个病",
        "A": "我爸去年冬天做了心脏支架手术",
        "B": "住院那半个月我每天只睡四小时，人都瘦脱相了",
        "desc": "住院 → 当时的疲惫",
    },
    {
        "query": "那个启动会",
        "A": "上周三开了星火项目的启动会",
        "B": "张伟拍胸脯说文档下周交，结果拖了整整三天",
        "desc": "启动会 → 会上的承诺落空",
    },
    {
        "query": "我养的那只猫",
        "A": "我养了只橘猫叫团子",
        "B": "它半夜总踩我脸上要吃的，还打翻过三次水杯",
        "desc": "猫 → 猫的日常行为",
    },
    {
        "query": "大学宿舍",
        "A": "大学住的是六人间，在四楼",
        "B": "老四每天凌晨打呼噜，我们轮流用枕头砸他",
        "desc": "宿舍 → 室友打呼噜",
    },
]

DISTRACTORS = [
    "缓存层用的是Redis 7", "消息队列用的是Kafka",
    "前端框架用React 18", "后端框架用FastAPI",
    "监控用Prometheus", "日志用ELK",
    "代码仓库托管在GitLab", "持续集成用Jenkins",
    "数据库用的是PostgreSQL 15", "服务器部署在阿里云华东区",
    "团队的代码规范要求函数不超过50行",
    "客户要求所有接口响应时间低于200ms",
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

    ids = {}
    for d in DISTRACTORS:
        st.add(d, resolve_conflict=False)
    for c in CASES:
        ids[c["A"]] = st.add(c["A"], resolve_conflict=False).ep_id
        ids[c["B"]] = st.add(c["B"], resolve_conflict=False).ep_id

    print("=" * 94)
    print("E-142 链式展开的真实价值")
    print("=" * 94)
    print(f"\n库中 {st.count()} 条\n")

    # 先验证：B 与 query 的余弦确实低
    print("前提验证：B 与 query 的语义距离")
    print("-" * 94)
    print(f"{'查询':<18}{'A 余弦':<12}{'B 余弦':<12}{'B 是否够低'}")
    print("-" * 94)
    ok_premise = 0
    for c in CASES:
        qv = emb.encode(c["query"])[0]
        av = emb.encode(c["A"])[0]
        bv = emb.encode(c["B"])[0]
        ca = float(qv @ av / (np.linalg.norm(qv) * np.linalg.norm(av)))
        cb = float(qv @ bv / (np.linalg.norm(qv) * np.linalg.norm(bv)))
        low = cb < 0.55
        ok_premise += 1 if low else 0
        print(f"{c['query'][:16]:<18}{ca:<12.4f}{cb:<12.4f}{'✅' if low else '⚠ 偏高'}")
    print("-" * 94)
    print(f"  前提成立（B 余弦 < 0.55）: {ok_premise}/{len(CASES)}\n")

    # 对比单跳 vs 链式
    print("=" * 94)
    print("对比")
    print("=" * 94)
    print(f"{'查询':<16}{'模式':<8}{'A排名':<8}{'B排名':<8}{'召回数':<8}{'耗时'}")
    print("-" * 94)

    results = []
    for c in CASES:
        a_id, b_id = ids[c["A"]], ids[c["B"]]
        row = {"query": c["query"], "desc": c["desc"]}

        for mode, use_expand in [("单跳", False), ("链式", True)]:
            t0 = time.perf_counter()
            res = st.search(c["query"], top=5, expand=use_expand, hops=2)
            dt = (time.perf_counter() - t0) * 1000
            got = [ep.ep_id for ep, *_ in res]

            a_rank = (got.index(a_id) + 1) if a_id in got else 0
            b_rank = (got.index(b_id) + 1) if b_id in got else 0

            print(f"{c['query'][:14]:<16}{mode:<8}"
                  f"{('#' + str(a_rank)) if a_rank else '-':<8}"
                  f"{('#' + str(b_rank)) if b_rank else '未召回':<8}"
                  f"{len(res):<8}{dt:.1f}ms")

            row[mode] = {"a_rank": a_rank, "b_rank": b_rank,
                         "n": len(res), "ms": round(dt, 1)}

        row["gain"] = (row["单跳"]["b_rank"] == 0
                       and row["链式"]["b_rank"] > 0)
        results.append(row)
        print()

    # 汇总
    print("=" * 94)
    print("汇总")
    print("=" * 94)
    n_gain = sum(1 for r in results if r["gain"])
    n_both = sum(1 for r in results
                 if r["单跳"]["b_rank"] > 0 and r["链式"]["b_rank"] > 0)
    n_none = sum(1 for r in results
                 if r["单跳"]["b_rank"] == 0 and r["链式"]["b_rank"] == 0)

    print(f"\n  B 单跳就召回:      {n_both}/{len(results)}")
    print(f"  B 仅链式召回:      {n_gain}/{len(results)}  ← 展开的价值")
    print(f"  B 两者都没召回:    {n_none}/{len(results)}")

    avg1 = sum(r["单跳"]["ms"] for r in results) / len(results)
    avg2 = sum(r["链式"]["ms"] for r in results) / len(results)
    print(f"\n  耗时：单跳 {avg1:.1f}ms  链式 {avg2:.1f}ms"
          f"  （+{avg2 - avg1:.1f}ms, {avg2/avg1:.1f}×）")

    print(f"\n  逐条：")
    for r in results:
        if r["gain"]:
            mark = "✅ 展开有效"
            detail = f"B 从「未召回」→ #{r['链式']['b_rank']}"
        elif r["单跳"]["b_rank"] > 0:
            mark = "— 单跳已够"
            detail = f"B 单跳 #{r['单跳']['b_rank']}"
        else:
            mark = "❌ 都失败"
            detail = "展开也没找到 B"
        print(f"    {mark:<12}{r['desc']:<24}{detail}")

    out = {"experiment": "E-142", "n": len(results),
           "premise_ok": ok_premise,
           "gain": n_gain, "already": n_both, "miss": n_none,
           "avg_1hop_ms": round(avg1, 1),
           "avg_chain_ms": round(avg2, 1),
           "details": results}
    with open(os.path.join(RESULTS, "e142_expand.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    st.con.close()
    print(f"\n写入 {os.path.join(RESULTS, 'e142_expand.json')}")


if __name__ == "__main__":
    main()
