"""
E-68 三量分离的完整验证（v1.1）

依据 E-67：
  affect_word   日常误差 0.20（极准），但漏显式事件（"被裁了"→0）
  语义锚定      情绪类 6.11（接近人工 6.53），但日常虚高 3.27
  → 两者互补，说明"情绪"和"代价"必须分开计。

v1.1 的五个量（全部独立，各司其职）：
  cost        失去代价（显式信号 + 事件类型）
  affect_word 显式情绪词强度
  scene       场景具体度（能否被唤醒）
  valence     情绪方向
  triggers    触发场景词

验证三件事：
  A. 四条红线是否仍全过
  B. 五个量在真实语料上的分布是否合理
  C. "重要"的复合判定：用 cost 主导，affect 辅助 —— 是否比单一分数好
"""
from __future__ import annotations

import json
import os
import subprocess
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, "cosmos_mem"))
RESULTS = os.path.join(_HERE, "results")
WORK = os.path.join(_HERE, "work", "e68")
os.makedirs(WORK, exist_ok=True)
os.environ.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")

from affect_v3 import affect_word_score, scene_specific_score

# ---------- 语料：覆盖五类，含 E-67 发现的难例 ----------
CORPUS = [
    # 高 cost
    ("我电脑的登录密码是 8848", ""),
    ("家里的门锁密码是 3721", ""),
    ("下个月 15 号是项目的截止日期，逾期不候", ""),
    ("客户明确说这条需求不能改", ""),
    # 高 cost + 高 affect_word
    ("这个千万别搞错，搞错了要出大事", ""),
    ("这个 bug 反复出现，我快崩溃了", ""),
    # 高 cost 但 affect_word=0（E-67 的难例）
    ("我被裁了，那天晚上一个人在车里坐了很久", ""),
    ("钱包丢了，里面有身份证和所有的卡", ""),
    # 低 cost 高 affect
    ("孩子第一次叫妈妈的时候", "傍晚，家里的客厅"),
    ("小时候在老家院子里看夕阳，天是橘红色的", "橘红色的天，安静"),
    # 触发场景
    ("小时候被邻居家的狗追着咬过，小腿上留了疤", "夏天，午后的巷子"),
    ("第一次当众出丑，忘词站在台上，全场都看着我", "大会议室"),
    # 日常无关
    ("上周二午饭吃的是番茄炒蛋", ""),
    ("前天那个路人的衣服是深蓝色的", ""),
    # 会被取代
    ("接口参数现在是 0.85", ""),
]

QUERIES = [
    ("电脑密码是多少", 0),
    ("门锁密码", 1),
    ("什么时候截止", 2),
    ("客户有什么不能改的", 3),
    ("什么千万别搞错", 4),
    ("bug 崩溃", 5),
    ("被裁了", 6),
    ("钱包丢了", 7),
    ("孩子第一次叫妈妈", 8),
    ("小时候的夕阳", 9),
    ("被狗咬", 10),
    ("当众出丑", 11),
    ("午饭吃了什么", 12),
    ("路人衣服颜色", 13),
    ("接口参数", 14),
]
TRIGGER_TESTS = [
    ("看到一只大狗跑过来", 10),
    ("要上台发言了", 11),
    ("傍晚天空橘红色的", 9),
]


def main():
    from store_v1 import MemoryStore, COLS, SCHEMA_VERSION

    print("=" * 96)
    print("E-68 v1.1 三量分离完整验证")
    print("=" * 96)

    db = os.path.join(WORK, "v11.db")
    for suf in ("", "-wal", "-shm"):
        p = db + suf
        if os.path.exists(p):
            os.remove(p)

    st = MemoryStore(db)
    ids = []
    print("\n写入语料并计算五量...\n")
    print("-" * 96)
    print(f"{'#':<4}{'cost':<8}{'aff_word':<10}{'scene':<8}{'val':<7}"
          f"{'triggers':<14}{'内容'}")
    print("-" * 96)
    for text, atm in CORPUS:
        ep = st.add(text, atmosphere=atm)
        ids.append(ep.ep_id)
        aw, sign = affect_word_score(text)
        ss = scene_specific_score(text)
        st.con.execute("UPDATE episodes SET affect_peak=?, valence=? WHERE ep_id=?",
                       (aw, sign, ep.ep_id))
        st.con.commit()
        ep2 = st.get(ep.ep_id)
        print(f"{len(ids)-1:<4}{ep2.cost:<8.2f}{aw:<10.2f}{ss:<8.2f}"
              f"{sign:<+7.1f}{'/'.join(ep2.triggers)[:12]:<14}{text[:30]}")
    print("-" * 96)

    out = {"experiment": "E-68"}

    # ---------- B. 五量分布 ----------
    print("\n" + "-" * 96)
    print("B. 五量分离的效果（关键：cost 与 affect_word 是否真的独立）")
    print("-" * 96)
    costs = [st.get(e).cost for e in ids]
    affw = [affect_word_score(t)[0] for t, _ in CORPUS]
    scene = [scene_specific_score(t) for t, _ in CORPUS]

    def corr(a, b):
        n = len(a); ma = sum(a)/n; mb = sum(b)/n
        va = sum((x-ma)**2 for x in a); vb = sum((x-mb)**2 for x in b)
        if va == 0 or vb == 0: return 0.0
        return sum((a[i]-ma)*(b[i]-mb) for i in range(n))/((va*vb)**.5)

    print(f"  cost × affect_word 相关 = {corr(costs, affw):+.3f}")
    print(f"  cost × scene 相关       = {corr(costs, scene):+.3f}")
    print(f"  affect_word × scene     = {corr(affw, scene):+.3f}")
    print("  （理想：三者都接近 0，说明确实是独立维度）")
    out["independence"] = {"cost_affw": round(corr(costs, affw), 3),
                           "cost_scene": round(corr(costs, scene), 3),
                           "affw_scene": round(corr(affw, scene), 3)}

    # ---------- 复合判定是否优于单一分数 ----------
    print("\n" + "-" * 96)
    print("复合重要性 vs 单一分数")
    print("-" * 96)
    print(f"{'#':<4}{'cost':<8}{'affw':<8}{'单分=平均':<12}{'复合(cost主导)':<16}{'内容'}")
    print("-" * 96)
    for i, (t, _) in enumerate(CORPUS):
        single = (costs[i] + affw[i]) / 2
        combo = max(costs[i], 0.7 * costs[i] + 0.3 * affw[i])
        print(f"{i:<4}{costs[i]:<8.2f}{affw[i]:<8.2f}{single:<12.2f}"
              f"{combo:<16.2f}{t[:32]}")
    print("-" * 96)

    # ---------- 检索召回 ----------
    print("\n" + "=" * 96)
    print("检索召回")
    print("=" * 96)
    hit = 0
    for q, gi in QUERIES:
        res = st.search(q, top=5)
        got = [ep.ep_id for ep, *_ in res]
        ok = ids[gi] in got
        hit += 1 if ok else 0
        rank = got.index(ids[gi]) + 1 if ids[gi] in got else -1
        print(f"  {q:<20} 命中={ok}  排名={rank}")
    rec = hit / len(QUERIES)
    print(f"  召回率 = {rec:.4f}")
    out["recall"] = round(rec, 4)

    # ---------- 触发 ----------
    print("\n" + "-" * 96)
    print("情景触发")
    print("-" * 96)
    th = 0
    for q, gi in TRIGGER_TESTS:
        res = st.search(q, top=5)
        got = [ep.ep_id for ep, *_ in res]
        ok = ids[gi] in got
        th += 1 if ok else 0
        print(f"  {q:<20} 命中={ok}")
    print(f"  触发命中率 = {th/len(TRIGGER_TESTS):.2f}")
    out["trigger"] = round(th/len(TRIGGER_TESTS), 2)

    # ---------- A. 四条红线 ----------
    print("\n" + "=" * 96)
    print("A. 四条红线")
    print("=" * 96)
    db2 = os.path.join(WORK, "v11_mig.db")
    for suf in ("", "-wal", "-shm"):
        p = db2 + suf
        if os.path.exists(p):
            os.remove(p)
    r = subprocess.run([sys.executable,
                        os.path.join(_HERE, "cosmos_mem", "cli_v1.py"),
                        "--db", db, "migrate", db2],
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace")
    st2 = MemoryStore(db2)
    o1 = st.con.execute(f"SELECT {','.join(COLS)} FROM episodes ORDER BY ep_id").fetchall()
    o2 = st2.con.execute(f"SELECT {','.join(COLS)} FROM episodes ORDER BY ep_id").fetchall()
    same = (o1 == o2)
    v1 = st.con.execute("SELECT ep_id,vec FROM vectors ORDER BY ep_id").fetchall()
    v2 = st2.con.execute("SELECT ep_id,vec FROM vectors ORDER BY ep_id").fetchall()
    samev = (v1 == v2)
    samer = all(
        [ep.ep_id for ep, *_ in st.search(q, top=5)] ==
        [ep.ep_id for ep, *_ in st2.search(q, top=5)]
        for q, _ in QUERIES)
    print(f"  R1 迁移: 行一致={same} 向量一致={samev} 检索一致={samer}")

    n_before = st.count()
    st.con.execute("DELETE FROM vectors"); st.con.commit(); st._vec = None
    st.rebuild_vectors()
    samer2 = all(
        [ep.ep_id for ep, *_ in st.search(q, top=5)] ==
        [ep.ep_id for ep, *_ in st2.search(q, top=5)]
        for q, _ in QUERIES)
    print(f"  R3 索引可重建: {samer2}")

    old = ids[14]
    st.supersede(old, "接口参数最终确认为 0.58")
    n_after = st.count()
    epo = st.get(old)
    in_all = any(ep.ep_id == old for ep, *_ in
                 st.search("接口参数", top=5, include_invalid=True))
    print(f"  R4 数据不丢: 条数 {n_before}→{n_after}, "
          f"旧存在={epo is not None}, 可查={in_all}")

    checks = [("R1 迁移100%", same and samev and samer),
              ("R2 能力不降", rec >= 0.80),
              ("R3 索引可重建", samer2),
              ("R4 数据不丢", n_after == n_before + 1 and in_all)]
    print("\n  汇总：")
    for n, ok in checks:
        print(f"    {n:<16}{'✅' if ok else '❌'}")
    out["redline"] = {n: bool(v) for n, v in checks}
    out["all_pass"] = all(v for _, v in checks)

    with open(os.path.join(RESULTS, "e68_v11.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(f"\n写入 {os.path.join(RESULTS, 'e68_v11.json')}")


if __name__ == "__main__":
    main()
