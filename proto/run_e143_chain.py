"""
E-143 链式展开的正确实现：系统给候选，模型判"是否同一情景链"

E-141/E-142 暴露：现有 _expand 三条通路全部失效
  ① TRIGGER_SCENE 只有 13 个硬编码场景 → 真实文本几乎命中不了 → triggers 恒空
  ② 展开阈值 DECAY=0.75 太高 → B 余弦 0.4173 永远过不了
  ③ 情绪共振要求 affect≥0.15，中性叙事恒为 0

根因：**"这两条记忆是否属于同一情景链"是语义判断，规则写不出来。**

按用户职责划分原则重构：
  系统负责：
    · 用 seed 找到一跳候选（余弦 top-N，不做阈值判断）
    · 打包给模型（seed + 候选）
  模型负责：
    · 判断哪些候选与 seed 属于同一情景/事件链
    · 说明连接理由

本实验验证：
  ① 系统候选池能否覆盖到真正的链式目标
  ② 模型能否从候选里正确挑出链式相关项
"""
from __future__ import annotations

import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, "cosmos_mem"))
TASK = os.path.join(_HERE, "work", "e143")
os.makedirs(TASK, exist_ok=True)
RESULTS = os.path.join(_HERE, "results")

# (query, seed A, 链式目标 B, 说明)
CASES = [
    ("海边那次旅行", "去年夏天我们去了青岛海边玩",
     "在栈桥吃了烤鱿鱼，那天下着小雨", "海边 → 栈桥吃鱿鱼"),
    ("我爸那个病", "我爸去年冬天做了心脏支架手术",
     "住院那半个月我每天只睡四小时，人都瘦脱相了", "住院 → 当时的疲惫"),
    ("那个启动会", "上周三开了星火项目的启动会",
     "张伟拍胸脯说文档下周交，结果拖了整整三天", "启动会 → 承诺落空"),
    ("我养的那只猫", "我养了只橘猫叫团子",
     "它半夜总踩我脸上要吃的，还打翻过三次水杯", "猫 → 猫的日常"),
    ("大学宿舍", "大学住的是六人间，在四楼",
     "老四每天凌晨打呼噜，我们轮流用枕头砸他", "宿舍 → 室友打呼噜"),
    ("小时候的夏天", "小时候夏天傍晚在院子里吃饭，天还很亮",
     "外婆坐在竹椅上给我扇扇子，说天黑了就去睡觉", "夏天傍晚 → 外婆扇扇子"),
    ("进不去门", "家里的门锁密码是 3721",
     "上个月忘带钥匙被锁在门外，在楼道坐了两个小时", "门锁 → 被锁门外"),
]

DISTRACTORS = [
    "缓存层用的是Redis 7", "消息队列用的是Kafka",
    "前端框架用React 18", "后端框架用FastAPI",
    "监控用Prometheus", "日志用ELK",
    "代码仓库托管在GitLab", "持续集成用Jenkins",
    "数据库用的是PostgreSQL 15", "服务器部署在阿里云华东区",
    "团队的代码规范要求函数不超过50行",
    "客户要求所有接口响应时间低于200ms",
    "这个项目 15 号截止", "接口参数定的是0.85",
]


def main():
    from store_v2 import MemoryStore
    from store_v1 import Embedder
    import numpy as np

    db = os.path.join(TASK, "e143.db")
    for suf in ("", "-wal", "-shm"):
        p = db + suf
        if os.path.exists(p):
            os.remove(p)

    st = MemoryStore(db)
    emb = Embedder.get()

    ids = {}
    for d in DISTRACTORS:
        st.add(d, resolve_conflict=False)
    for q, a, b, desc in CASES:
        ids[a] = st.add(a, resolve_conflict=False).ep_id
        ids[b] = st.add(b, resolve_conflict=False).ep_id

    print("=" * 94)
    print("E-143 链式展开：系统给候选，模型判链条")
    print("=" * 94)
    print(f"\n库中 {st.count()} 条\n")

    st._load_vectors(include_invalid=False)
    norms = np.linalg.norm(st._vec, axis=1)
    norms[norms == 0] = 1.0

    packs = []
    print("① 系统候选池覆盖率（不做阈值判断，纯 top-N）")
    print("-" * 94)
    print(f"{'说明':<26}{'B 与 seed 余弦':<16}{'B 在 top-20':<14}{'B 在 top-50'}")
    print("-" * 94)

    for i, (q, a, b, desc) in enumerate(CASES):
        seed_id, b_id = ids[a], ids[b]
        sv = emb.encode(a)[0]
        sims = (st._vec @ sv) / norms
        b_idx = st._vec_ids.index(b_id)
        b_sim = float(sims[b_idx])

        order = np.argsort(-sims)
        b_rank = int(np.where(order == b_idx)[0][0]) + 1

        print(f"{desc:<26}{b_sim:<16.4f}"
              f"{('✅ #' + str(b_rank)) if b_rank <= 20 else ('❌ #' + str(b_rank)):<14}"
              f"{'✅' if b_rank <= 50 else '❌ #' + str(b_rank)}")

        # 生成候选池（top-20）
        topk = 20
        idxs = order[:topk]
        cands = []
        for j in idxs:
            eid = st._vec_ids[int(j)]
            if eid == seed_id:
                continue
            ep = st.get(eid)
            if ep:
                cands.append({"text": ep.raw_text,
                              "cos": round(float(sims[int(j)]), 4)})

        packs.append({
            "cid": f"C{i:02d}",
            "desc": desc,
            "query": q,
            "seed": a,
            "gold_chain_target": b,
            "gold_rank_in_candidates": next(
                (k + 1 for k, c in enumerate(cands) if c["text"] == b), 0),
            "candidates": cands,
        })

    st.con.close()

    with open(os.path.join(TASK, "packs.json"), "w", encoding="utf-8") as f:
        json.dump(packs, f, ensure_ascii=False, indent=1)

    print("\n" + "=" * 94)
    print("汇总")
    print("=" * 94)
    in20 = sum(1 for p in packs if 0 < p["gold_rank_in_candidates"] <= 20)
    print(f"  B 进入 top-20 候选池: {in20}/{len(packs)}")
    print(f"  B 在候选池里的平均排名: "
          f"{sum(p['gold_rank_in_candidates'] for p in packs if p['gold_rank_in_candidates']) / max(1, in20):.1f}")

    print(f"\n  任务包 → {TASK}/packs.json（{len(packs)} 组）")
    print(f"\n  关键：系统只做 top-N 取数，不设阈值；")
    print(f"        哪些候选属于同一情景链，交给模型判断。")


if __name__ == "__main__":
    main()
