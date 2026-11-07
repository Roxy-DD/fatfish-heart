"""
E-114 论文 Law II「逆元素索引」的落地验证

来源：The_Dialectical_Unity_Principle_via_Cosmos_Net.pdf
  Law II: Inverse Element Indexing Construction
  三段分支：
    Reincarnation（轮回）：强相似 + 值不同 → 矛盾 → 新建节点，旧的不删
    Assimilation（同化） ：强相似 + 值相同 → 加强
    Genesis（创生）      ：不相似           → 新建

论文原值：resonance_threshold = 0.85

本实验两件事：
  1. 按论文原值 0.85 跑 → 验证它是否失效（我们 E-7 实测同类均cos=0.7538）
  2. 用我们的数据标定阈值 → 看是否真能区分「矛盾 / 同义 / 无关」三类

判据：在标注好的三组样本上，三段分支的分类准确率。
"""
from __future__ import annotations

import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, "cosmos_mem"))
RESULTS = os.path.join(_HERE, "results")

# 标注好的三组样本：每对（旧句, 新句, 期望分支）
PAIRS = [
    # ---- 矛盾 → 应判 Reincarnation（值不同）----
    ("我电脑的登录密码是 8848", "我把电脑密码改成 9988 了", "reincarnation"),
    ("家里的门锁密码是 3721", "门锁密码换成了 9512", "reincarnation"),
    ("这个项目 15 号截止", "项目截止延到 20 号", "reincarnation"),
    ("我的车是白色卡罗拉", "我换车了，现在是黑色大众", "reincarnation"),
    ("我住在城东花园3栋", "我搬家到城西新村5号", "reincarnation"),
    ("接口参数定的是 0.85", "接口参数改成了 1.2", "reincarnation"),
    ("明天下午三点开会", "会议改到明天上午十点", "reincarnation"),
    ("我女儿上学期中班", "我女儿这学期上大班了", "reincarnation"),

    # ---- 同义/补充 → 应判 Assimilation（同值，只是措辞或补充）----
    ("我电脑的登录密码是 8848", "电脑登录密码是8848", "assimilation"),
    ("家里的门锁密码是 3721", "家门密码3721", "assimilation"),
    ("这个项目 15 号截止", "项目截止日期是15号", "assimilation"),
    ("我的车是白色卡罗拉", "我的车是辆白色卡罗拉", "assimilation"),
    ("我住在城东花园3栋", "家住城东花园3栋", "assimilation"),
    ("接口参数定的是 0.85", "接口参数为0.85", "assimilation"),
    ("明天下午三点开会", "明天15点开会", "assimilation"),
    ("我女儿上学期中班", "我女儿在上中班", "assimilation"),

    # ---- 无关 → 应判 Genesis ----
    ("我电脑的登录密码是 8848", "这个bug反复出现我快崩溃了", "genesis"),
    ("家里的门锁密码是 3721", "小时候被狗追着咬过", "genesis"),
    ("这个项目 15 号截止", "我妈说家里一切都好", "genesis"),
    ("我的车是白色卡罗拉", "上周二午饭吃的是番茄炒蛋", "genesis"),
    ("我住在城东花园3栋", "医生说要多喝水", "genesis"),
    ("接口参数定的是 0.85", "孩子的生日是6月1号", "genesis"),
    ("明天下午三点开会", "仓库里还有两百件库存", "genesis"),
    ("我女儿上学期中班", "服务器部署在阿里云华东区", "genesis"),
]


def main():
    from store_v1 import Embedder
    import numpy as np

    print("=" * 84)
    print("E-114 论文 Law II 逆元素索引：落地验证")
    print("=" * 84)
    print(f"\n标注样本 {len(PAIRS)} 对（矛盾8 / 同义8 / 无关8）\n")

    emb = Embedder.get()

    # ---------- 第 1 步：测量三类对的真实余弦分布 ----------
    print("-" * 84)
    print("① 三类样本的余弦分布（这决定阈值能否成立）")
    print("-" * 84)

    groups = {"reincarnation": [], "assimilation": [], "genesis": []}
    per_pair = []

    for old, new, expect in PAIRS:
        v_old = emb.encode(old)[0]
        v_new = emb.encode(new)[0]
        cos = float(np.dot(v_old, v_new))
        groups[expect].append(cos)
        per_pair.append({"old": old, "new": new, "expect": expect, "cos": cos})

    print(f"{'组':<16}{'n':<6}{'均值':<10}{'最小':<10}{'最大':<10}")
    print("-" * 84)
    for g, vals in groups.items():
        print(f"{g:<16}{len(vals):<6}{np.mean(vals):<10.4f}"
              f"{np.min(vals):<10.4f}{np.max(vals):<10.4f}")
    print("-" * 84)

    m_re = float(np.mean(groups["reincarnation"]))
    m_as = float(np.mean(groups["assimilation"]))
    m_ge = float(np.mean(groups["genesis"]))
    print(f"\n关键观察：")
    print(f"  矛盾对均cos = {m_re:.4f}")
    print(f"  同义对均cos = {m_as:.4f}")
    print(f"  无关对均cos = {m_ge:.4f}")
    print(f"  同义 - 矛盾 差 = {m_as - m_re:.4f}")
    print(f"  矛盾 - 无关 差 = {m_re - m_ge:.4f}")

    # ---------- 第 2 步：按论文原值 0.85 分类 ----------
    print("\n" + "=" * 84)
    print("② 按论文原值 threshold=0.85 分类")
    print("=" * 84)

    def classify(cos, threshold, same_value):
        """论文三段分支：需要一个'值是否相同'的判定"""
        if cos > threshold:
            return "assimilation" if same_value else "reincarnation"
        return "genesis"

    def accuracy_at(th, use_oracle_value=True):
        """use_oracle_value=True 时用人工标注的'值是否相同'（理想上限）"""
        hit = 0
        for p in per_pair:
            same = (p["expect"] == "assimilation")
            pred = classify(p["cos"], th, same)
            if pred == p["expect"]:
                hit += 1
        return hit / len(per_pair)

    acc85 = accuracy_at(0.85)
    print(f"\n  论文阈值 0.85 的准确率: {acc85:.4f}  ({int(acc85*len(per_pair))}/{len(per_pair)})")

    # 看看 0.85 到底触发了几次
    trig = sum(1 for p in per_pair if p["cos"] > 0.85)
    print(f"  余弦 > 0.85 的对数: {trig}/{len(per_pair)}")
    if trig == 0:
        print(f"  → ⚠️ 阈值 0.85 一次都没触发！所有对都判成 genesis")
        print(f"     这意味着论文的机制在真实 BGE 向量上完全失效")

    # ---------- 第 3 步：在我们的数据上标定阈值 ----------
    print("\n" + "=" * 84)
    print("③ 用我们的数据标定阈值")
    print("=" * 84)

    print(f"\n{'threshold':<12}{'准确率':<12}{'触发数'}")
    print("-" * 84)
    best_th, best_acc = 0, 0
    for th in [x / 100 for x in range(50, 96, 3)]:
        acc = accuracy_at(th)
        trig = sum(1 for p in per_pair if p["cos"] > th)
        mark = ""
        if acc > best_acc:
            best_acc, best_th = acc, th
            mark = " ←"
        print(f"{th:<12.2f}{acc:<12.4f}{trig}{mark}")
    print("-" * 84)
    print(f"\n  最优阈值: {best_th:.2f}  准确率 {best_acc:.4f}")

    # ---------- 第 4 步：关键结论 ----------
    print("\n" + "=" * 84)
    print("④ 结论")
    print("=" * 84)
    print(f"  论文原值 0.85 : 准确率 {acc85:.4f}  触发 {trig} 次")
    print(f"  标定后 {best_th:.2f}   : 准确率 {best_acc:.4f}")
    print(f"  提升: {best_acc - acc85:+.4f}")

    if acc85 < 0.4 and best_acc > 0.7:
        print(f"\n  → 论文给的算法结构可用，但阈值必须重标定")
        print(f"  → 这是'文献只是借势，必须自己跑'的又一个实例")
    elif best_acc <= 0.7:
        print(f"\n  → 即使标定后准确率仍不足，说明纯余弦无法区分矛盾的三种情形")
        print(f"  → 需要引入语义判定（模型）")

    out = {"experiment": "E-114",
           "n_pairs": len(PAIRS),
           "group_mean": {"reincarnation": round(m_re, 4),
                          "assimilation": round(m_as, 4),
                          "genesis": round(m_ge, 4)},
           "paper_threshold_085": {"accuracy": round(acc85, 4), "triggered": trig},
           "calibrated": {"threshold": best_th, "accuracy": round(best_acc, 4)},
           "pairs": per_pair}
    with open(os.path.join(RESULTS, "e114_law2.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(f"\n写入 {os.path.join(RESULTS, 'e114_law2.json')}")


if __name__ == "__main__":
    main()
