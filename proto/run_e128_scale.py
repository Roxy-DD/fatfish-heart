"""
E-128 大样本压测：候选包质量是否随库规模下降

这是这套机制的命门：
  库越大，相似但不相关的记忆越多，候选包越容易被挤占，
  模型看不到真目标 → 只能判 none。

设计：
  规模 100 / 300 / 1000 条干扰记忆 + 10 条目标记忆
  对每条目标写一条矛盾记忆，检查：
    ① 系统机械耗时（候选打包）随规模的变化
    ② 真目标是否进入 top-10 候选包（这是硬指标）
    ③ 目标在候选包里的排名

干扰记忆要"像真的"：同领域、含数字、含常见槽位词，
不能是随机噪声——否则测不出真实难度。
"""
from __future__ import annotations

import json
import os
import sys
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, "cosmos_mem"))
TASK = os.path.join(_HERE, "work", "e128")
os.makedirs(TASK, exist_ok=True)
RESULTS = os.path.join(_HERE, "results")

# ------------------------------------------------------------
# 10 条目标记忆 + 它们的矛盾版本
# ------------------------------------------------------------
TARGETS = [
    ("我的车是白色卡罗拉", "我换车了现在是黑色大众", "replace"),
    ("我住在城东花园3栋", "我搬家到城西新村5号", "replace"),
    ("家里的门锁密码是 3721", "门锁密码换成了9512", "replace"),
    ("接口参数定的是0.85", "接口参数改成了1.2", "replace"),
    ("这个项目 15 号截止", "客户同意把截止延到20号", "replace"),
    ("明天下午三点开会", "会议改到明天上午十点", "replace"),
    ("我电脑的登录密码是 8848", "我把电脑密码改成了9988", "replace"),
    ("客户要求界面必须支持深色模式", "客户还要求界面必须支持大字体", "merge"),
    ("我要连续吃两周的药", "另外我晚上也要吃一次药", "merge"),
    ("老王说门锁密码是 3721", "小李说门锁密码是9512", "conflict"),
]

# ------------------------------------------------------------
# 干扰记忆生成：同领域 + 含数字 + 含槽位词，但都是"别的对象"
# ------------------------------------------------------------
CITIES = ["城东", "城西", "城南", "城北", "新城", "老城", "滨江", "湖畔"]
GARDENS = ["花园", "小区", "新村", "家园", "家园", "苑"]
STREETS = ["中山路", "人民路", "解放路", "建设路", "长江路", "黄河路"]
CARS = ["白色", "黑色", "银色", "红色", "蓝色", "灰色"]
BRANDS = ["卡罗拉", "大众", "本田", "丰田", "日产", "别克", "现代", "标致"]
PROJECTS = ["星火", "曙光", "灯塔", "鲲鹏", "青云", "北斗", "天问", "潜龙"]
NAMES = ["张伟", "李娜", "王强", "赵敏", "刘洋", "陈静", "杨帆", "周涛",
         "吴磊", "郑爽", "孙悦", "马超", "朱琳", "胡军", "林峰", "何静"]
ITEMS = ["显示器", "打印机", "路由器", "键盘", "鼠标", "音箱", "摄像头", "投影仪"]


def gen_distractors(n):
    """生成 n 条同领域干扰记忆"""
    import random
    rng = random.Random(20261102)

    out = []
    kinds = ["addr", "car", "pwd", "param", "deadline", "meeting",
             "online", "hardware", "person", "project"]

    while len(out) < n:
        k = rng.choice(kinds)
        if k == "addr":
            out.append(f"{rng.choice(NAMES)}住在{rng.choice(CITIES)}"
                       f"{rng.choice(GARDENS)}{rng.randint(1, 30)}栋")
        elif k == "car":
            out.append(f"{rng.choice(NAMES)}的车是{rng.choice(CARS)}"
                       f"{rng.choice(BRANDS)}")
        elif k == "pwd":
            out.append(f"{rng.choice(NAMES)}的{rng.choice(['邮箱','网盘','门禁','WiFi','后台'])}"
                       f"密码是 {rng.randint(1000, 9999)}")
        elif k == "param":
            out.append(f"{rng.choice(ITEMS)}的参数定的是 "
                       f"{rng.choice(['0.5','0.7','0.9','1.1','1.5','2.0'])}")
        elif k == "deadline":
            out.append(f"{rng.choice(PROJECTS)}项目 {rng.randint(1,28)} 号截止")
        elif k == "meeting":
            out.append(f"{rng.choice(['明天','后天','本周三','本周五'])}"
                       f"{rng.choice(['上午九点','上午十点','下午两点','下午四点','晚上七点'])}"
                       f"开{rng.choice(['评审会','周会','对齐会','复盘会','启动会'])}")
        elif k == "online":
            out.append(f"{rng.choice(PROJECTS)}项目的负责人是{rng.choice(NAMES)}")
        elif k == "hardware":
            out.append(f"{rng.choice(NAMES)}领了台{rng.choice(ITEMS)}")
        elif k == "person":
            out.append(f"{rng.choice(NAMES)}的工号是{rng.choice(['A','B','C','D'])}"
                       f"{rng.randint(1000, 9999)}")
        elif k == "project":
            out.append(f"{rng.choice(PROJECTS)}项目预算{rng.randint(10, 200)}万")

    return out[:n]


def run_scale(n_distractors):
    """在指定规模下测候选包质量"""
    from store_v2 import MemoryStore
    from store_v1 import Embedder
    import conflict

    db = os.path.join(TASK, f"e128_{n_distractors}.db")
    for suf in ("", "-wal", "-shm"):
        p = db + suf
        if os.path.exists(p):
            os.remove(p)

    st = MemoryStore(db)
    emb = Embedder.get()

    # 写干扰 + 目标
    distractors = gen_distractors(n_distractors)
    t0 = time.perf_counter()
    for d in distractors:
        st.add(d, resolve_conflict=False)
    t_write_dist = time.perf_counter() - t0

    target_ids = {}
    for tgt, _, _ in TARGETS:
        ep = st.add(tgt, resolve_conflict=False)
        target_ids[tgt] = ep.ep_id

    total_mem = len(distractors) + len(TARGETS)

    # 逐条矛盾记忆，检查候选包
    rows = []
    t_pack_total = 0.0
    for tgt, new, expect in TARGETS:
        t0 = time.perf_counter()
        pack = conflict.prepare_candidates(st, emb, new, top_k=20)
        dt = time.perf_counter() - t0
        t_pack_total += dt

        # 目标是否在候选包里
        in_pack = any(c.text == tgt for c in pack.candidates)
        rank = next((i + 1 for i, c in enumerate(pack.candidates)
                     if c.text == tgt), 0)
        top_cos = pack.candidates[0].cos if pack.candidates else 0.0
        tgt_cos = next((c.cos for c in pack.candidates if c.text == tgt), 0.0)

        rows.append({
            "target": tgt, "new": new, "expect": expect,
            "in_pack": in_pack, "rank": rank,
            "top_cos": round(top_cos, 4),
            "target_cos": round(tgt_cos, 4),
            "pack_ms": round(dt * 1000, 2),
        })

    st.con.close()

    n_in = sum(1 for r in rows if r["in_pack"])
    avg_pack_ms = (t_pack_total / len(TARGETS)) * 1000

    return {
        "scale": total_mem,
        "n_distractors": n_distractors,
        "write_distractors_s": round(t_write_dist, 1),
        "avg_pack_ms": round(avg_pack_ms, 2),
        "target_in_pack": n_in,
        "total_cases": len(TARGETS),
        "hit_rate": round(n_in / len(TARGETS), 4),
        "avg_rank": round(
            sum(r["rank"] for r in rows if r["rank"]) /
            max(1, n_in), 2),
        "rows": rows,
    }


def main():
    print("=" * 92)
    print("E-128 大样本压测：候选包质量 vs 库规模")
    print("=" * 92)
    print(f"\n{len(TARGETS)} 条矛盾用例 × 三种规模（100/300/1000 条干扰）\n")

    results = []
    for n in [100, 300, 1000]:
        print("-" * 92)
        print(f"规模：{n} 条干扰 + {len(TARGETS)} 条目标 = {n + len(TARGETS)} 条")
        print("-" * 92)
        r = run_scale(n)
        results.append(r)

        print(f"  写入干扰耗时: {r['write_distractors_s']} s")
        print(f"  候选打包平均耗时: {r['avg_pack_ms']} ms")
        print(f"  真目标进入候选包: {r['target_in_pack']}/{r['total_cases']}"
              f" = {r['hit_rate']:.4f}")
        print(f"  目标平均排名: {r['avg_rank']}")
        print()
        print(f"  {'用例':<30}{'在包?':<8}{'排名':<6}{'首位cos':<10}{'目标cos'}")
        print(f"  {'-' * 66}")
        for row in r["rows"]:
            print(f"  {row['new'][:28]:<30}{'✓' if row['in_pack'] else '✗':<8}"
                  f"{row['rank'] or '-':<6}{row['top_cos']:<10}{row['target_cos']}")

    # ---------- 汇总 ----------
    print("\n" + "=" * 92)
    print("汇总：规模对候选包质量的影响")
    print("=" * 92)
    print(f"{'库规模':<12}{'打包耗时':<14}{'目标命中率':<14}{'平均排名'}")
    print("-" * 92)
    for r in results:
        pack_s = "%.2f ms" % r["avg_pack_ms"]
        hit_s = "%.4f" % r["hit_rate"]
        print(f"{r['scale']:<12}{pack_s:<14}{hit_s:<14}{r['avg_rank']}")

    base = results[0]["hit_rate"]
    print()
    for r in results[1:]:
        delta = r["hit_rate"] - base
        print(f"  {r['scale']} 条 vs 100 条：命中率变化 {delta:+.4f}")

    worst = min(r["hit_rate"] for r in results)
    print(f"\n  最低命中率: {worst:.4f}")
    if worst == 1.0:
        print("  → ✅ 所有规模下真目标都进候选包，机制在规模上稳定")
    else:
        print(f"  → ⚠️ 大规模下有目标被挤出候选包，需要扩 top_k 或改进候选策略")

    out = {"experiment": "E-128",
           "scales": [r["scale"] for r in results],
           "results": results}
    with open(os.path.join(RESULTS, "e128_scale.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(f"\n写入 {os.path.join(RESULTS, 'e128_scale.json')}")


if __name__ == "__main__":
    main()
