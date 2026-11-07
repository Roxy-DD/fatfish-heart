"""
E-60 严格验证情绪估计器：加一个"人工标注"基准，算真实相关性

E-59 的问题：
  ① 我只报了各组的平均，没算"逐句一致性"
  ② SNOWNLP 把 NEUTRAL 判得比 NEG 还负（−0.270 vs −0.250）—— 这是个严重信号
  ③ 没有混入真实难度（含蓄句）

E-60 做法：
  给 32 句人工标注 valence（−2..+2）和 arousal（0..2），作为基准。
  然后算三种估计器与人工标注的 Spearman 相关。
  另加：把 NEUTRAL 里的"重要但无情绪"句子单列，看是否被误判为负面。
"""
from __future__ import annotations

import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)
sys.path.insert(0, os.path.join(_HERE, "cosmos_mem"))
RESULTS = os.path.join(_HERE, "results")

# (文本, 类别, 人工 valence -2..2, 人工 arousal 0..2)
DATA = [
    # POS
    ("今天项目上线成功了，所有人都特别开心", "POS", 2.0, 1.8),
    ("他考上了理想的学校，全家都为他高兴", "POS", 2.0, 1.6),
    ("这顿饭做得太好了，我很满意", "POS", 1.5, 1.2),
    ("终于把这个问题解决了，太爽了", "POS", 1.8, 1.7),
    ("孩子第一次叫妈妈，我又激动又想哭", "POS", 2.0, 2.0),
    ("这次合作非常顺利，客户很满意", "POS", 1.5, 1.0),
    ("我升职了，晚上要和朋友们庆祝", "POS", 1.8, 1.6),
    ("收到他送的花，心里暖暖的", "POS", 1.6, 1.1),
    # NEG
    ("这个千万别搞错，搞错了要出大事", "NEG", -1.5, 1.9),
    ("项目又延期了，老板非常生气", "NEG", -1.8, 1.8),
    ("体检报告有问题，我很害怕", "NEG", -1.9, 1.9),
    ("他被公司裁员了，整个人都垮了", "NEG", -2.0, 1.7),
    ("我们又吵架了，她现在不想理我", "NEG", -1.7, 1.6),
    ("这个 bug 反复出现，我快崩溃了", "NEG", -1.6, 1.8),
    ("钱包丢了，里面有身份证和所有卡", "NEG", -1.8, 1.7),
    ("父亲住院了，情况不太好", "NEG", -2.0, 1.8),
    # NEUTRAL（中性事实；其中后 3 句"重要但无情绪"）
    ("明天下午三点在会议室开会", "NEUTRAL", 0.0, 0.3),
    ("这份文档已经发到你的邮箱了", "NEUTRAL", 0.0, 0.1),
    ("接口参数定为 0.85", "NEUTRAL", 0.0, 0.1),
    ("他今年三十四岁，住在城东", "NEUTRAL", 0.0, 0.1),
    ("仓库里还有两百件库存", "NEUTRAL", 0.0, 0.2),
    ("会议改到周四上午十点", "IMPORTANT", 0.0, 0.6),
    ("密码是 8848，别忘了", "IMPORTANT", 0.0, 0.9),
    ("截止日期是下个月 15 号，逾期不候", "IMPORTANT", -0.3, 1.0),
    # SUBTLE（含蓄情绪，没有情绪词但有情绪）
    ("他说完那句话，会议室安静了很久", "SUBTLE", -1.0, 0.8),
    ("她只是点了点头，没有再说什么", "SUBTLE", -0.8, 0.5),
    ("看到那条消息，我把手机放下了", "SUBTLE", -1.2, 0.9),
    ("我把那封信重新折好，放回抽屉", "SUBTLE", -1.0, 0.7),
    ("窗外下着雨，我坐了很久没动", "SUBTLE", -0.7, 0.4),
    ("他笑了笑，说没事，然后转身走了", "SUBTLE", -1.1, 0.8),
    ("那年冬天特别冷，我一直记得", "SUBTLE", -0.5, 0.6),
    ("照片里的人都在笑，只有他不看镜头", "SUBTLE", -0.9, 0.6),
]


def spearman(a, b):
    """Spearman 秩相关（不依赖 scipy）"""
    import numpy as np
    a = np.asarray(a, float); b = np.asarray(b, float)
    n = len(a)
    ra = np.argsort(np.argsort(a)).astype(float)
    rb = np.argsort(np.argsort(b)).astype(float)
    ra -= ra.mean(); rb -= rb.mean()
    den = np.sqrt((ra ** 2).sum() * (rb ** 2).sum())
    return float((ra * rb).sum() / den) if den else 0.0


def rule_est(text):
    from fixes import estimate_affect_v2
    a = estimate_affect_v2(text)
    return a["valence"], a["arousal"]


def sn_est(text):
    from snownlp import SnowNLP
    try:
        p = float(SnowNLP(text).sentiments)
    except Exception:
        p = 0.5
    return (p - 0.5) * 2.0, abs(p - 0.5) * 2.0


def main():
    print("=" * 96)
    print("E-60 情绪估计器严格验证（对人工标注）")
    print("=" * 96)
    n = len(DATA)
    cats = {}
    for _, c, _, _ in DATA:
        cats[c] = cats.get(c, 0) + 1
    print(f"\n{n} 句：", "  ".join(f"{k}={v}" for k, v in cats.items()))

    gold_v = [d[2] for d in DATA]
    gold_a = [d[3] for d in DATA]

    # 归一化到同一量纲（-1..1 / 0..1）以便比较
    def norm_v(x):
        return max(-1.0, min(1.0, x / 2.0))

    def norm_a(x):
        return max(0.0, min(1.0, x / 2.0))

    ests = {"RULE": rule_est, "SNOWNLP": sn_est}
    rows = {}
    for name, fn in ests.items():
        vs, ars = [], []
        for text, *_ in DATA:
            v, a = fn(text)
            vs.append(v); ars.append(a)
        # 与人工标注（归一化后）的相关
        gv = [norm_v(x) for x in gold_v]
        ga = [norm_a(x) for x in gold_a]
        rows[name] = {
            "v": vs, "a": ars,
            "spearman_v": round(spearman(vs, gv), 4),
            "spearman_a": round(spearman(ars, ga), 4),
        }

    print("\n" + "-" * 96)
    print("与人工标注的 Spearman 相关")
    print("-" * 96)
    print(f"{'方法':<12}{'效价相关':<14}{'唤醒相关':<14}{'判读'}")
    print("-" * 96)
    for name, r in rows.items():
        sv = r["spearman_v"]
        quality = ("好" if sv >= 0.7 else "中" if sv >= 0.4 else "差")
        print(f"{name:<12}{sv:<+14.3f}{r['spearman_a']:<+14.3f}{quality}")
    print("-" * 96)

    # ---------- 分组均值（关键：NEUTRAL vs IMPORTANT vs NEG）----------
    print("\n" + "-" * 96)
    print("分组平均效价（这是最关键的判读）")
    print("-" * 96)
    print(f"{'方法':<12}{'POS':<10}{'NEG':<10}{'NEUTRAL':<11}"
          f"{'IMPORTANT':<12}{'SUBTLE':<10}")
    print("-" * 96)
    grp = {}
    for name, r in rows.items():
        line = f"{name:<12}"
        g = {}
        for cat in ["POS", "NEG", "NEUTRAL", "IMPORTANT", "SUBTLE"]:
            idx = [i for i, d in enumerate(DATA) if d[1] == cat]
            if not idx:
                continue
            m = sum(r["v"][i] for i in idx) / len(idx)
            g[cat] = round(m, 3)
            line += f"{m:<+10.3f}" if cat != "IMPORTANT" else f"{m:<+12.3f}"
        grp[name] = g
        print(line)
    print("-" * 96)

    # 关键检验：NEUTRAL 是否被误判为负
    print("\n关键检验：")
    for name, g in grp.items():
        sn_neu = g.get("NEUTRAL", 0)
        sn_neg = g.get("NEG", 0)
        bad = sn_neu < sn_neg      # 中性被判得比负面还负
        print(f"  {name:<10} NEUTRAL={sn_neu:+.3f}  NEG={sn_neg:+.3f}  "
              f"→ {'⚠ 中性比负面还负（严重错误）' if bad else '正常'}")

    # 含蓄组
    print("\n含蓄组（没有情绪词但有情绪）:")
    for name, r in rows.items():
        idx = [i for i, d in enumerate(DATA) if d[1] == "SUBTLE"]
        m = sum(r["v"][i] for i in idx) / len(idx)
        g = sum(norm_v(gold_v[i]) for i in idx) / len(idx)
        print(f"  {name:<10} 估计={m:+.3f}  人工={g:+.3f}  偏差={m-g:+.3f}")

    # ---------- 结论 ----------
    print("\n" + "=" * 96)
    print("结论")
    print("=" * 96)
    best = max(rows.items(), key=lambda kv: kv[1]["spearman_v"])
    print(f"  效价相关最好: {best[0]} (ρ={best[1]['spearman_v']:+.3f})")
    for name, r in rows.items():
        print(f"    {name}: 效价 ρ={r['spearman_v']:+.3f}  唤醒 ρ={r['spearman_a']:+.3f}")
    print(f"\n  → 两者都不够好（ρ<0.7 就不能用于生产）" if best[1]["spearman_v"] < 0.7
          else f"\n  → {best[0]} 可用")

    out = {"experiment": "E-60", "n": n,
           "spearman": {k: {kk: v[kk] for kk in ("spearman_v", "spearman_a")}
                        for k, v in rows.items()},
           "group_mean": grp,
           "note": "人工标注为基准；IMPORTANT=重要但无情绪"}
    with open(os.path.join(RESULTS, "e60_affect_strict.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(f"\n写入 {os.path.join(RESULTS, 'e60_affect_strict.json')}")


if __name__ == "__main__":
    main()
