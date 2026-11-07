"""
E-131 万条压测：top_k=20 在 1 万条规模下是否仍然够用

E-128/E-130 只在 1000 条规模下验证过。
真实使用中记忆会持续增长，必须确认边界。

测三种规模：1000 / 5000 / 10000
每条规模测：目标全量排名 + 各 top_k 命中率
"""
from __future__ import annotations

import json
import os
import random
import sys
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, "cosmos_mem"))
TASK = os.path.join(_HERE, "work", "e131")
os.makedirs(TASK, exist_ok=True)
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

CITIES = ["城东", "城西", "城南", "城北", "新城", "老城", "滨江", "湖畔",
          "东湖", "西湖", "南山", "北苑", "金桥", "银湖", "翠苑", "文华"]
GARDENS = ["花园", "小区", "新村", "家园", "苑", "府", "庭", "里"]
CARS = ["白色", "黑色", "银色", "红色", "蓝色", "灰色", "金色", "棕色"]
BRANDS = ["卡罗拉", "大众", "本田", "丰田", "日产", "别克", "现代", "标致",
          "福特", "雪佛兰", "马自达", "起亚"]
PROJECTS = ["星火", "曙光", "灯塔", "鲲鹏", "青云", "北斗", "天问", "潜龙",
            "惊蛰", "白露", "小满", "霜降", "谷雨", "立秋", "冬至", "春分"]
NAMES = ["张伟", "李娜", "王强", "赵敏", "刘洋", "陈静", "杨帆", "周涛",
         "吴磊", "郑爽", "孙悦", "马超", "朱琳", "胡军", "林峰", "何静",
         "高翔", "罗萍", "梁宇", "宋佳", "唐磊", "许薇", "韩冰", "冯雷"]
ITEMS = ["显示器", "打印机", "路由器", "键盘", "鼠标", "音箱", "摄像头",
         "投影仪", "扫描仪", "复印机"]
PWDTYPE = ["邮箱", "网盘", "门禁", "WiFi", "后台", "数据库", "服务器", "OA"]
MTG = ["评审会", "周会", "对齐会", "复盘会", "启动会", "总结会", "碰头会"]
TIME_PT = ["上午九点", "上午十点", "上午十一点", "下午两点", "下午三点",
           "下午四点", "下午五点", "晚上七点", "晚上八点"]
DAY = ["明天", "后天", "本周三", "本周五", "下周一", "下周三"]


def gen_distractors(n, seed=20261102):
    rng = random.Random(seed)
    kinds = ["addr", "car", "pwd", "param", "deadline", "meeting",
             "online", "hardware", "person", "project", "phone", "bill"]
    out = []
    seen = set()
    while len(out) < n:
        k = rng.choice(kinds)
        if k == "addr":
            s = (f"{rng.choice(NAMES)}住在{rng.choice(CITIES)}"
                 f"{rng.choice(GARDENS)}{rng.randint(1, 40)}栋")
        elif k == "car":
            s = f"{rng.choice(NAMES)}的车是{rng.choice(CARS)}{rng.choice(BRANDS)}"
        elif k == "pwd":
            s = (f"{rng.choice(NAMES)}的{rng.choice(PWDTYPE)}"
                 f"密码是 {rng.randint(1000, 9999)}")
        elif k == "param":
            s = (f"{rng.choice(ITEMS)}的参数定的是 "
                 f"{rng.choice(['0.3','0.5','0.7','0.9','1.1','1.3','1.5','2.0'])}")
        elif k == "deadline":
            s = f"{rng.choice(PROJECTS)}项目 {rng.randint(1, 28)} 号截止"
        elif k == "meeting":
            s = (f"{rng.choice(DAY)}{rng.choice(TIME_PT)}"
                 f"开{rng.choice(MTG)}")
        elif k == "online":
            s = f"{rng.choice(PROJECTS)}项目的负责人是{rng.choice(NAMES)}"
        elif k == "hardware":
            s = f"{rng.choice(NAMES)}领了台{rng.choice(ITEMS)}"
        elif k == "person":
            s = (f"{rng.choice(NAMES)}的工号是"
                 f"{rng.choice(['A','B','C','D'])}{rng.randint(1000, 9999)}")
        elif k == "project":
            s = f"{rng.choice(PROJECTS)}项目预算{rng.randint(10, 300)}万"
        elif k == "phone":
            s = f"{rng.choice(NAMES)}的电话是13{rng.randint(10000000, 99999999)}"
        else:
            s = f"{rng.choice(PROJECTS)}项目的合同金额是{rng.randint(10, 99)}.5万"

        if s in seen:
            continue
        seen.add(s)
        out.append(s)
    return out


def run_scale(n_dist, top_ks):
    from store_v2 import MemoryStore
    from store_v1 import Embedder
    import numpy as np

    db = os.path.join(TASK, f"e131_{n_dist}.db")
    for suf in ("", "-wal", "-shm"):
        p = db + suf
        if os.path.exists(p):
            os.remove(p)

    st = MemoryStore(db)
    emb = Embedder.get()

    print(f"\n{'=' * 90}")
    print(f"规模 {n_dist} 条干扰 + {len(TARGETS)} 条目标")
    print(f"{'=' * 90}")

    # 分批写入（避免单事务过大）
    dists = gen_distractors(n_dist)
    t0 = time.perf_counter()
    BATCH = 200
    for i in range(0, len(dists), BATCH):
        for d in dists[i:i + BATCH]:
            st.add(d, resolve_conflict=False)
    t_write = time.perf_counter() - t0
    print(f"  写入干扰: {t_write:.1f} s ({len(dists)/t_write:.0f} 条/秒)")

    id_of_text = {}
    for tgt, _ in TARGETS:
        ep = st.add(tgt, resolve_conflict=False)
        id_of_text[tgt] = ep.ep_id

    rows = st.con.execute(
        "SELECT ep_id, raw_text FROM episodes WHERE invalid_at IS NULL").fetchall()
    text_of = {e: t for e, t in rows}
    total = len(rows)

    st._load_vectors(include_invalid=False)
    norms = np.linalg.norm(st._vec, axis=1)
    norms[norms == 0] = 1.0

    # 算每条目标的全量排名
    ranks = {}
    pack_times = {k: [] for k in top_ks}
    import conflict

    for tgt, new in TARGETS:
        tgt_id = id_of_text.get(tgt)
        nv = emb.encode(new)[0]
        nv_norm = float(np.linalg.norm(nv)) or 1.0
        sims = (st._vec @ nv) / (norms * nv_norm)
        tgt_idx = st._vec_ids.index(tgt_id)
        order = np.argsort(-sims)
        rank = int(np.where(order == tgt_idx)[0][0]) + 1
        ranks[new] = (tgt, rank)

        # 各 top_k 的实际打包耗时与命中
        for k in top_ks:
            t1 = time.perf_counter()
            pack = conflict.prepare_candidates(st, emb, new, top_k=k)
            pack_times[k].append(time.perf_counter() - t1)

    st.con.close()

    print(f"\n  库总记录: {total}")
    print(f"\n  {'用例':<32}{'目标全量排名'}")
    print(f"  {'-' * 60}")
    for tgt, new in TARGETS:
        if new in ranks:
            print(f"  {new[:30]:<32}#{ranks[new][1]}")

    max_rank = max(r for _, r in ranks.values())

    print(f"\n  目标最差排名: #{max_rank}")
    print(f"\n  {'top_k':<10}{'命中率':<14}{'平均打包耗时'}")
    print(f"  {'-' * 60}")
    out = []
    for k in top_ks:
        hit = sum(1 for _, r in ranks.values() if r <= k)
        rate = hit / len(ranks)
        avg_ms = (sum(pack_times[k]) / len(pack_times[k])) * 1000
        out.append({"top_k": k, "hit": hit, "rate": round(rate, 4),
                    "avg_ms": round(avg_ms, 2)})
        print(f"  {k:<10}{f'{hit}/{len(ranks)} = {rate:.2f}':<14}{avg_ms:.2f} ms")

    return {"scale": total, "n_distractors": n_dist,
            "max_rank": max_rank,
            "ranks": {k: v[1] for k, v in ranks.items()},
            "topk_table": out}


def main():
    print("=" * 90)
    print("E-131 万条压测：top_k 边界确认")
    print("=" * 90)

    TOP_KS = [10, 20, 30, 50, 100]
    results = []
    for n in [1000, 5000, 10000]:
        results.append(run_scale(n, TOP_KS))

    # ---------- 汇总 ----------
    print("\n" + "=" * 90)
    print("汇总：规模 vs 所需 top_k")
    print("=" * 90)
    print(f"{'库规模':<10}{'最差排名':<12}" +
          "".join(f"{'k=' + str(k):<10}" for k in TOP_KS))
    print("-" * 90)
    for r in results:
        cells = "".join(f"{t['rate']:.2f}{'✅' if t['rate'] == 1.0 else ' ':<8}"
                        for t in r["topk_table"])
        print(f"{r['scale']:<10}#{r['max_rank']:<11}{cells}")

    print("\n" + "-" * 90)
    print("各规模下 100% 命中的最小 top_k：")
    for r in results:
        full = [t["top_k"] for t in r["topk_table"] if t["rate"] == 1.0]
        print(f"  规模 {r['scale']}: "
              f"{'top_k=' + str(min(full)) if full else '即使 top_k=100 也不够'}")

    out = {"experiment": "E-131", "results": results}
    with open(os.path.join(RESULTS, "e131_10k.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(f"\n写入 {os.path.join(RESULTS, 'e131_10k.json')}")


if __name__ == "__main__":
    main()
