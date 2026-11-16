"""
E-64 情绪强度估计：关键词 vs BGE 原型锚定

问题（E-63 暴露）：
  关键词法对以下句子给出 affect=0.0：
    "小时候被邻居家的狗追着咬过，小腿上留了疤"   （E-62 人工 T=8）
    "第一次当众出丑，忘词站在台上，全场都看着我" （T=7）
    "亲人去世那天我在医院走廊站了很久"         （T=9）
  因为这些句子没有"害怕""崩溃"这类显式情绪词。

三种方法对比（在同一批句子上）：
  M1 KEYWORD   现有关键词表
  M2 PROTOTYPE BGE 原型锚定：与"高唤醒原型"和"低唤醒原型"的相似度差
  M3 EMOWORD   BGE 与情绪词向量的相似度

评测基准：E-62 的 T 评分（人工/模型评的触发强度 0-10），
只取其中可以对齐的条目。
"""
from __future__ import annotations

import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, "cosmos_mem"))
RESULTS = os.path.join(_HERE, "results")
MODEL_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "models", "bge-small-zh")

# ---------- 高唤醒原型（不含情绪词，靠情境）----------
PROTO_HIGH = [
    "那一刻我整个人定在原地，手心全是汗",
    "周围的声音好像突然消失了，只听见自己的心跳",
    "腿在发抖，我一句话都说不出来",
    "我把东西摔在地上，转身就走",
    "眼泪一下就出来了，我控制不住",
    "所有人都看着我，我脑子里一片空白",
    "接到电话的那一刻，我坐在楼梯上很久没有起来",
    "那道疤到现在还在，每次看到都会想起那天",
]
# ---------- 低唤醒原型 ----------
PROTO_LOW = [
    "这份文档已经发到你的邮箱了",
    "会议改到周四上午十点",
    "仓库里还有两百件库存",
    "表格第三列需要填日期",
    "这个参数配置在配置文件里",
    "他今年三十四岁，住在城东",
    "上周二午饭吃的是番茄炒蛋",
    "那件衣服是深蓝色的",
]

# ---------- 情绪词 ----------
EMO_WORDS = ["害怕", "恐惧", "崩溃", "痛苦", "难过", "愤怒", "羞耻",
             "开心", "喜悦", "温暖", "心碎", "后悔", "怀念", "激动"]

# ---------- 评测集：文本 + 人工 T（来自 E-62 的同类条目，重新组合成完整句）----------
# 每条：(文本, 人工T)
EVAL = [
    ("小时候被邻居家的狗追着咬过，小腿上留了疤", 8),
    ("看到大型犬冲过来，我立刻停住了脚步", 7),
    ("第一次当众出丑，忘词站在台上，全场都看着我", 7),
    ("要在很多人面前发言，我提前三天就开始紧张", 6),
    ("亲人去世那天，我在医院走廊站了很久", 9),
    ("闻到医院消毒水的味道，我还是会愣一下", 7),
    ("高考失利出分的那天，我把自己关在房间里", 5),
    ("小时候走丢在商场，找不到家里人", 6),
    ("小时候在老家院子里看夕阳，天是橘红色的", 6),
    ("傍晚看到橘红色的天，我突然想起那个院子", 7),
    ("毕业典礼那天，所有人都在拍照", 5),
    ("第一次拿到工资请家人吃饭", 5),
    ("和喜欢的人第一次约会，走过那条街", 7),
    ("孩子第一次叫妈妈的时候", 8),
    ("和好朋友通宵聊天的那晚", 5),
    # 硬事实（低情绪）
    ("家里的门锁密码是 3721", 1),
    ("我电脑的登录密码是 8848", 1),
    ("下个月 15 号是截止日期", 2),
    ("家里的住址是城东花园 3 栋", 1),
    ("银行卡密码是六位数", 2),
    # 日常无关（低情绪）
    ("上周二午饭吃的是番茄炒蛋", 0),
    ("前天那个路人的衣服是深蓝色的", 0),
    ("三个月前看过一个饮料广告", 0),
    ("昨天刷到一个搞笑视频", 1),
    ("去年某天下午是阴天", 0),
]


def pearson(a, b):
    n = len(a)
    ma, mb = sum(a) / n, sum(b) / n
    va = sum((x - ma) ** 2 for x in a)
    vb = sum((x - mb) ** 2 for x in b)
    if va == 0 or vb == 0:
        return 0.0
    return sum((a[i] - ma) * (b[i] - mb) for i in range(n)) / (va ** .5 * vb ** .5)


def spearman(a, b):
    def rank(v):
        order = sorted(range(len(v)), key=lambda i: v[i])
        r = [0.0] * len(v)
        i = 0
        while i < len(order):
            j = i
            while j + 1 < len(order) and v[order[j + 1]] == v[order[i]]:
                j += 1
            avg = (i + j) / 2 + 1
            for k in range(i, j + 1):
                r[order[k]] = avg
            i = j + 1
        return r
    return pearson(rank(a), rank(b))


def main():
    import numpy as np
    import torch
    from transformers import AutoTokenizer, AutoModel
    torch.set_num_threads(4)
    tok = AutoTokenizer.from_pretrained(MODEL_DIR)
    mdl = AutoModel.from_pretrained(MODEL_DIR).eval()

    def enc(texts):
        if isinstance(texts, str):
            texts = [texts]
        with torch.no_grad():
            e = tok(texts, padding=True, truncation=True, max_length=128,
                    return_tensors="pt")
            o = mdl(**e)
        v = o.last_hidden_state[:, 0].numpy().astype("float32")
        n = np.linalg.norm(v, axis=1, keepdims=True); n[n == 0] = 1
        return v / n

    texts = [t for t, _ in EVAL]
    gold = [g for _, g in EVAL]

    print("=" * 92)
    print("E-64 情绪强度估计：关键词 vs BGE 原型锚定")
    print("=" * 92)
    print(f"\n评测 {len(EVAL)} 句，基准为人工触发强度 T(0-10)\n")

    # ---------- M1 关键词 ----------
    from store_v1 import estimate_affect_peak
    m1 = [estimate_affect_peak(t)["peak"] for t in texts]
    m1_10 = [x * 10 for x in m1]

    # ---------- M2 原型锚定 ----------
    Vh = enc(PROTO_HIGH); Vl = enc(PROTO_LOW)
    ch = Vh.mean(axis=0); ch /= np.linalg.norm(ch)
    cl = Vl.mean(axis=0); cl /= np.linalg.norm(cl)
    V = enc(texts)
    sh = V @ ch; sl = V @ cl
    m2_raw = sh - sl                      # 相对高唤醒原型的突出度
    # 归一化到 0-10
    lo_, hi_ = m2_raw.min(), m2_raw.max()
    m2_10 = ((m2_raw - lo_) / (hi_ - lo_) * 10).tolist()

    # ---------- M3 情绪词相似度 ----------
    Ve = enc(EMO_WORDS)
    ce = Ve.mean(axis=0); ce /= np.linalg.norm(ce)
    m3_raw = V @ ce
    lo3, hi3 = m3_raw.min(), m3_raw.max()
    m3_10 = ((m3_raw - lo3) / (hi3 - lo3) * 10).tolist()

    print("-" * 92)
    print("与人工 T 的相关性")
    print("-" * 92)
    print(f"{'方法':<14}{'Pearson':<12}{'Spearman':<12}")
    print("-" * 92)
    res = {}
    for name, arr in (("M1 KEYWORD", m1_10), ("M2 PROTOTYPE", m2_10),
                      ("M3 EMOWORD", m3_10)):
        p, s = pearson(arr, gold), spearman(arr, gold)
        res[name] = {"pearson": round(p, 4), "spearman": round(s, 4)}
        print(f"{name:<14}{p:<+12.3f}{s:<+12.3f}")
    print("-" * 92)

    # ---------- 关键：失败案例 ----------
    print("\n关键失败案例（E-63 里 affect=0 的句子）")
    print("-" * 92)
    print(f"{'文本':<38}{'人工T':<8}{'M1':<8}{'M2':<8}{'M3':<8}")
    print("-" * 92)
    for i, (t, g) in enumerate(EVAL[:15]):
        print(f"{t[:36]:<38}{g:<8}{m1_10[i]:<8.1f}{m2_10[i]:<8.1f}{m3_10[i]:<8.1f}")
    print("-" * 92)

    # ---------- 分组 ----------
    print("\n分组均值")
    print("-" * 92)
    groups = {"情绪类(0-14)": list(range(0, 15)),
              "硬事实(15-19)": list(range(15, 20)),
              "日常无关(20-24)": list(range(20, 25))}
    print(f"{'组':<18}{'人工T':<10}{'M1':<10}{'M2':<10}{'M3':<10}")
    print("-" * 92)
    for gname, gi in groups.items():
        gt = sum(gold[i] for i in gi) / len(gi)
        a1 = sum(m1_10[i] for i in gi) / len(gi)
        a2 = sum(m2_10[i] for i in gi) / len(gi)
        a3 = sum(m3_10[i] for i in gi) / len(gi)
        print(f"{gname:<18}{gt:<10.2f}{a1:<10.2f}{a2:<10.2f}{a3:<10.2f}")
    print("-" * 92)

    # ---------- 结论 ----------
    print("\n" + "=" * 92)
    print("结论")
    print("=" * 92)
    best = max(res.items(), key=lambda kv: kv[1]["pearson"])
    print(f"  最好方法: {best[0]}  Pearson={best[1]['pearson']:+.3f}")
    for name, r in res.items():
        print(f"    {name:<14} ρ={r['pearson']:+.3f}")
    usable = best[1]["pearson"] >= 0.7
    print(f"\n  → {'可用（ρ≥0.7）' if usable else '仍不可用（ρ<0.7），需换思路'}")

    out = {"experiment": "E-64", "n": len(EVAL), "results": res,
           "raw": {"gold": gold, "M1": m1_10, "M2": m2_10, "M3": m3_10},
           "best": best[0], "usable": bool(usable)}
    with open(os.path.join(RESULTS, "e64_affect_methods.json"), "w",
              encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(f"\n写入 {os.path.join(RESULTS, 'e64_affect_methods.json')}")


if __name__ == "__main__":
    main()
