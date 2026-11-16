"""
E-59 情绪估计器对比：规则法 vs SnowNLP vs BGE 语义嵌入

背景（docs/51 P0）：
  v0.4 用关键词表估情绪，覆盖太窄——"妈妈打电话说一切都好"识别不出任何情绪。

三种做法：
  RULE   现有关键词规则（基线）
  SNOWNLP 通用中文情感分类器（0-1 极性）
  BGE+PROBE  用 BGE 向量 + 少量人工标注做线性探针

评测集（我自己构造，分四类，每类 8 句 = 32 句）：
  POS       明显正面（喜悦/欣慰）
  NEG       明显负面（恐惧/焦虑/愤怒）
  NEUTRAL   中性事实（无情绪但可能重要）
  SUBTLE    **含蓄情绪**（关键难点：没有情绪词，但有情绪）
            例："他说完那句话，会议室安静了很久"

指标：
  1. 区分度：POS 组均分 vs NEG 组均分 的差
  2. 含蓄组识别：SUBTLE 组是否被识别为"有情绪"
  3. 与人工标注的一致性（Spearman）
"""
from __future__ import annotations

import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)
RESULTS = os.path.join(_HERE, "results")
MODEL_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "models", "bge-small-zh")

# ---------- 评测集 ----------
POS = [
    "今天项目上线成功了，所有人都特别开心",
    "他考上了理想的学校，全家都为他高兴",
    "这顿饭做得太好了，我很满意",
    "终于把这个问题解决了，太爽了",
    "孩子第一次叫妈妈，我又激动又想哭",
    "这次合作非常顺利，客户很满意",
    "我升职了，晚上要和朋友们庆祝",
    "收到他送的花，心里暖暖的",
]
NEG = [
    "这个千万别搞错，搞错了要出大事",
    "项目又延期了，老板非常生气",
    "体检报告有问题，我很害怕",
    "他被公司裁员了，整个人都垮了",
    "我们又吵架了，她现在不想理我",
    "这个 bug 反复出现，我快崩溃了",
    "钱包丢了，里面有身份证和所有卡",
    "父亲住院了，情况不太好",
]
NEUTRAL = [
    "明天下午三点在会议室开会",
    "这份文档已经发到你的邮箱了",
    "接口参数定为 0.85",
    "他今年三十四岁，住在城东",
    "仓库里还有两百件库存",
    "会议改到周四上午十点",
    "这个表格第三列需要填日期",
    "服务器地址是 192.168.1.10",
]
SUBTLE = [
    "他说完那句话，会议室安静了很久",
    "她只是点了点头，没有再说什么",
    "看到那条消息，我把手机放下了",
    "我把那封信重新折好，放回抽屉",
    "窗外下着雨，我坐了很久没动",
    "他笑了笑，说没事，然后转身走了",
    "那年冬天特别冷，我一直记得",
    "照片里的人都在笑，只有他不看镜头",
]

SETS = {"POS": POS, "NEG": NEG, "NEUTRAL": NEUTRAL, "SUBTLE": SUBTLE}


def rule_score(text):
    """复用 store 里的规则法，返回 (valence, arousal)"""
    sys.path.insert(0, os.path.join(_HERE, "cosmos_mem"))
    from fixes import estimate_affect_v2
    a = estimate_affect_v2(text)
    return a["valence"], a["arousal"]


def snownlp_score(text):
    """SnowNLP 返回 0-1 正面概率；映射到 valence -1..1"""
    from snownlp import SnowNLP
    try:
        p = float(SnowNLP(text).sentiments)
    except Exception:
        p = 0.5
    return (p - 0.5) * 2.0, abs(p - 0.5) * 2.0


def main():
    print("=" * 92)
    print("E-59 情绪估计器对比")
    print("=" * 92)
    n_total = sum(len(v) for v in SETS.values())
    print(f"\n评测集 {n_total} 句（4 类 × 8）\n")

    # ---------- 1. 规则法 ----------
    print("-" * 92)
    print("① RULE 规则法")
    print("-" * 92)
    print(f"{'类别':<10}{'平均效价':<12}{'平均唤醒':<12}{'唤醒>0.1比例'}")
    print("-" * 92)
    rule = {}
    for name, sents in SETS.items():
        vals, arous = [], []
        for s in sents:
            v, a = rule_score(s)
            vals.append(v); arous.append(a)
        hit = sum(1 for a in arous if a > 0.1) / len(arous)
        rule[name] = {"val": sum(vals) / len(vals), "aro": sum(arous) / len(arous),
                      "detect": hit}
        print(f"{name:<10}{sum(vals)/len(vals):<+12.3f}{sum(arous)/len(arous):<12.3f}{hit:.2f}")
    print("-" * 92)
    sep_rule = rule["POS"]["val"] - rule["NEG"]["val"]
    print(f"  区分度（POS效价 − NEG效价）= {sep_rule:+.3f}")
    print(f"  含蓄组识别率 = {rule['SUBTLE']['detect']:.2f}")

    # ---------- 2. SnowNLP ----------
    print("\n" + "-" * 92)
    print("② SNOWNLP 通用情感分类器")
    print("-" * 92)
    print(f"{'类别':<10}{'平均效价':<12}{'平均唤醒':<12}{'唤醒>0.1比例'}")
    print("-" * 92)
    sn = {}
    for name, sents in SETS.items():
        vals, arous = [], []
        for s in sents:
            v, a = snownlp_score(s)
            vals.append(v); arous.append(a)
        hit = sum(1 for a in arous if a > 0.1) / len(arous)
        sn[name] = {"val": sum(vals) / len(vals), "aro": sum(arous) / len(arous),
                    "detect": hit}
        print(f"{name:<10}{sum(vals)/len(vals):<+12.3f}{sum(arous)/len(arous):<12.3f}{hit:.2f}")
    print("-" * 92)
    sep_sn = sn["POS"]["val"] - sn["NEG"]["val"]
    print(f"  区分度 = {sep_sn:+.3f}")
    print(f"  含蓄组识别率 = {sn['SUBTLE']['detect']:.2f}")

    # ---------- 3. BGE 语义嵌入的距离 ----------
    print("\n" + "-" * 92)
    print("③ BGE 语义空间：各类内部一致性 + 类间距离")
    print("-" * 92)
    import numpy as np
    import torch
    from transformers import AutoTokenizer, AutoModel
    torch.set_num_threads(4)
    tok = AutoTokenizer.from_pretrained(MODEL_DIR)
    mdl = AutoModel.from_pretrained(MODEL_DIR).eval()

    def enc(texts):
        with torch.no_grad():
            e = tok(texts, padding=True, truncation=True, max_length=128,
                    return_tensors="pt")
            o = mdl(**e)
        v = o.last_hidden_state[:, 0].numpy().astype("float32")
        n = np.linalg.norm(v, axis=1, keepdims=True); n[n == 0] = 1
        return v / n

    cents = {}
    for name, sents in SETS.items():
        V = enc(sents)
        c = V.mean(axis=0)
        c /= np.linalg.norm(c)
        cents[name] = c
        within = float((V @ c).mean())
        print(f"  {name:<10}类内平均相似 {within:.4f}")

    print("\n  类间质心相似度矩阵：")
    names = list(SETS.keys())
    print("      " + "".join(f"{n:<10}" for n in names))
    for a in names:
        row = f"{a:<6}"
        for b in names:
            row += f"{float(cents[a] @ cents[b]):<10.3f}"
        print(row)
    print("-" * 92)

    # ---------- 汇总 ----------
    print("\n" + "=" * 92)
    print("汇总结论")
    print("=" * 92)
    print(f"{'方法':<14}{'区分度':<12}{'含蓄组识别':<14}{'结论'}")
    print("-" * 92)
    print(f"{'RULE':<14}{sep_rule:<+12.3f}{rule['SUBTLE']['detect']:<14.2f}"
          f"{'仅认关键词' if rule['SUBTLE']['detect'] < 0.3 else '较好'}")
    print(f"{'SNOWNLP':<14}{sep_sn:<+12.3f}{sn['SUBTLE']['detect']:<14.2f}"
          f"{'通用但含蓄识别弱' if sn['SUBTLE']['detect'] < 0.5 else '较好'}")
    print("-" * 92)

    print("\n判读要点：")
    print("  · 区分度 = POS 与 NEG 的效价差，越大越好（理想 > 1.0）")
    print("  · 含蓄组识别 = 没有情绪词但有情绪的句子被识别为'有情绪'的比例")
    print("  · RULE 若含蓄组接近 0 → 证明关键词法覆盖太窄（预期如此）")

    out = {"experiment": "E-59", "n": n_total,
           "rule": {k: {kk: round(vv, 4) for kk, vv in v.items()}
                    for k, v in rule.items()},
           "snownlp": {k: {kk: round(vv, 4) for kk, vv in v.items()}
                       for k, v in sn.items()},
           "separation": {"rule": round(sep_rule, 4), "snownlp": round(sep_sn, 4)},
           "centroid_sim": {a: {b: round(float(cents[a] @ cents[b]), 4)
                                for b in names} for a in names}}
    with open(os.path.join(RESULTS, "e59_affect.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(f"\n写入 {os.path.join(RESULTS, 'e59_affect.json')}")


if __name__ == "__main__":
    main()
