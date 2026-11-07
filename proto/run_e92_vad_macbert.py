"""
E-92 实测 vad-macbert（中文 VAD 回归）能否补上情绪强度短板

背景：
  情绪强度是我剩下的三大瓶颈之一，此前三次失败：
    M1 关键词        ρ=0.000（情绪句全判0）
    M2 SnowNLP       ρ=0.290（比规则还差）
    M3 语义原型锚定   ρ=0.801（但测的是"具体感"，日常类虚高3.3）

候选：Pectics/vad-macbert（中文 VAD 回归，chinese-macbert-base）
  输出 3 个连续值：valence / arousal / dominance
  README 自报 pearson ~0.73，但自认"标签从英文迁移，可能不匹配中文标注"

实测方案：
  用 E-60 的 25 句（人工标注效价+唤醒）作为 gold，
  对比 vad-macbert 预测的 valence/arousal 与人工标注的 Spearman 相关。
  同时对比之前的 SnowNLP 成绩（ρ_valence=0.290，ρ_arousal=0.184）。

红线注意：
  这是纯派生层（情绪估计），不影响迁移/不遗忘/索引重建。
  但若接入，需重跑 E-87 确认四红线。
"""
from __future__ import annotations

import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
RESULTS = os.path.join(_HERE, "results")

# E-60 的评测集（文本, 人工效价-2..2, 人工唤醒0..2）
EVAL = [
    ("今天项目上线成功了，所有人都特别开心", 2.0, 1.8),
    ("他考上了理想的学校，全家都为他高兴", 2.0, 1.6),
    ("这顿饭做得太好了，我很满意", 1.5, 1.2),
    ("终于把这个问题解决了，太爽了", 1.8, 1.7),
    ("孩子第一次叫妈妈，我又激动又想哭", 2.0, 2.0),
    ("这次合作非常顺利，客户很满意", 1.5, 1.0),
    ("我升职了，晚上要和朋友们庆祝", 1.8, 1.6),
    ("收到他送的花，心里暖暖的", 1.6, 1.1),
    ("这个千万别搞错，搞错了要出大事", -1.5, 1.9),
    ("项目又延期了，老板非常生气", -1.8, 1.8),
    ("体检报告有问题，我很害怕", -1.9, 1.9),
    ("他被公司裁员了，整个人都垮了", -2.0, 1.7),
    ("我们又吵架了，她现在不想理我", -1.7, 1.6),
    ("这个 bug 反复出现，我快崩溃了", -1.6, 1.8),
    ("钱包丢了，里面有身份证和所有卡", -1.8, 1.7),
    ("父亲住院了，情况不太好", -2.0, 1.8),
    ("明天下午三点在会议室开会", 0.0, 0.3),
    ("这份文档已经发到你的邮箱了", 0.0, 0.1),
    ("接口参数定为 0.85", 0.0, 0.1),
    ("他今年三十四岁，住在城东", 0.0, 0.1),
    ("仓库里还有两百件库存", 0.0, 0.2),
    ("会议改到周四上午十点", 0.0, 0.6),
    ("密码是 8848，别忘了", 0.0, 0.9),
    ("截止日期是下个月 15 号，逾期不候", -0.3, 1.0),
    ("他说完那句话，会议室安静了很久", -1.0, 0.8),
]


def spearman(a, b):
    def rk(v):
        o = sorted(range(len(v)), key=lambda i: v[i]); r = [0.0] * len(v); i = 0
        while i < len(o):
            j = i
            while j + 1 < len(o) and v[o[j + 1]] == v[o[i]]:
                j += 1
            avg = (i + j) / 2 + 1
            for k in range(i, j + 1):
                r[o[k]] = avg
            i = j + 1
        return r
    n = len(a); ma = sum(a) / n; mb = sum(b) / n
    ra = rk(a); rb = rk(b)
    va = sum((x - (n + 1) / 2) ** 2 for x in ra)
    vb = sum((x - (n + 1) / 2) ** 2 for x in rb)
    if va == 0 or vb == 0:
        return 0.0
    cov = sum((ra[i] - (n + 1) / 2) * (rb[i] - (n + 1) / 2) for i in range(n))
    return cov / (va ** 0.5 * vb ** 0.5)


def main():
    print("=" * 92)
    print("E-92 实测 vad-macbert（中文 VAD 回归）")
    print("=" * 92)

    import torch
    from transformers import AutoTokenizer, AutoModelForSequenceClassification

    model_id = "Pectics/vad-macbert"
    print(f"\n加载模型 {model_id} ...")
    print("  （首次会从 hf-mirror.com 下载，macbert-base ~400MB）")

    tok = AutoTokenizer.from_pretrained(model_id)
    mdl = AutoModelForSequenceClassification.from_pretrained(model_id)
    mdl.eval()

    texts = [t for t, _, _ in EVAL]
    gold_v = [v for _, v, _ in EVAL]
    gold_a = [a for _, _, a in EVAL]

    pred_v, pred_a = [], []
    with torch.no_grad():
        for t in texts:
            inp = tok(t, return_tensors="pt", padding=True, truncation=True,
                      max_length=128)
            out = mdl(**inp)
            vad = out.logits.squeeze().tolist()
            pred_v.append(vad[0])
            pred_a.append(vad[1])

    sv = spearman(pred_v, gold_v)
    sa = spearman(pred_a, gold_a)

    print("\n" + "=" * 92)
    print("与人工标注的 Spearman 相关")
    print("=" * 92)
    print(f"{'模型':<16}{'效价ρ':<12}{'唤醒ρ':<12}")
    print("-" * 92)
    print(f"{'vad-macbert':<16}{sv:<+12.3f}{sa:<+12.3f}")
    print(f"{'SnowNLP(对照)':<16}{0.290:<+12.3f}{0.184:<+12.3f}")
    print(f"{'关键词(对照)':<16}{0.000:<+12.3f}{0.000:<+12.3f}")
    print("-" * 92)

    # 逐条看
    print("\n逐条（前 16 条情绪句）：")
    print(f"{'文本':<32}{'人工V':<8}{'预测V':<10}{'人工A':<8}{'预测A':<10}")
    print("-" * 92)
    for i in range(min(16, len(texts))):
        print(f"{texts[i][:30]:<32}{gold_v[i]:<+8.1f}{pred_v[i]:<+10.2f}"
              f"{gold_a[i]:<8.1f}{pred_a[i]:<10.2f}")

    # 中性句
    print("\n中性句（后 9 条，看是否虚高）：")
    print(f"{'文本':<32}{'人工V':<8}{'预测V':<10}{'人工A':<8}{'预测A':<10}")
    print("-" * 92)
    for i in range(16, len(texts)):
        print(f"{texts[i][:30]:<32}{gold_v[i]:<+8.1f}{pred_v[i]:<+10.2f}"
              f"{gold_a[i]:<8.1f}{pred_a[i]:<10.2f}")

    # 中性组虚高检查
    neu_v = [pred_v[i] for i in range(16, len(texts))]
    neu_a = [pred_a[i] for i in range(16, len(texts))]
    import statistics
    print(f"\n中性组预测均值：valence={statistics.mean(neu_v):.3f} "
          f"arousal={statistics.mean(neu_a):.3f}（人工应接近 0/低）")

    # 结论
    print("\n" + "=" * 92)
    print("结论")
    print("=" * 92)
    usable = sv >= 0.7 and sa >= 0.5
    if sv >= 0.7 and sa >= 0.5:
        print(f"  ✅ vad-macbert 效价ρ={sv:.3f} 唤醒ρ={sa:.3f}，可用")
        print(f"  → 显著优于 SnowNLP（0.29/0.18）和关键词（0.00/0.00）")
    elif sv >= 0.5:
        print(f"  ⚠️ 效价可用（ρ={sv:.3f}），唤醒偏弱（ρ={sa:.3f}）")
    else:
        print(f"  ❌ 效价ρ={sv:.3f} 唤醒ρ={sa:.3f}，仍不可用，需换思路")

    out = {"experiment": "E-92", "n": len(texts),
           "spearman_valence": round(sv, 4), "spearman_arousal": round(sa, 4),
           "baseline_snownlp": {"valence": 0.290, "arousal": 0.184},
           "baseline_keyword": {"valence": 0.0, "arousal": 0.0},
           "pred_valence": [round(x, 4) for x in pred_v],
           "pred_arousal": [round(x, 4) for x in pred_a],
           "gold_valence": gold_v, "gold_arousal": gold_a,
           "usable": bool(usable)}
    with open(os.path.join(RESULTS, "e92_vad_macbert.json"), "w",
              encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(f"\n写入 {os.path.join(RESULTS, 'e92_vad_macbert.json')}")


if __name__ == "__main__":
    main()
