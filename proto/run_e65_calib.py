"""
E-65 原型锚定的严格验证 + 校准方案对比

E-64 的问题：min-max 归一化把最低分拉到 0，导致"日常无关"组虚高（3.30）。
E-65 直接测 v2 的绝对尺度版本，并对比三种校准：

  CAL-A  min-max      （E-64 用的，会虚高）
  CAL-B  绝对 sigmoid （v2 用的，c/T 由原型自身定）
  CAL-C  双原型 z 分数：(sh - sl - c) / sd_lo，sd_lo 来自低原型自身的波动

指标：
  1. 与人工 T 的 Pearson / Spearman
  2. 三组均值是否分开（情绪组高、硬事实中低、日常低）
  3. 关键失败：日常无关组是否被误判为高
"""
from __future__ import annotations

import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, "cosmos_mem"))
RESULTS = os.path.join(_HERE, "results")
MODEL_DIR = r"C:\Users\xuee\Desktop\肥鱼之心\models\bge-small-zh"

# 与 E-64 同一评测集（25 句），保证可比
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
    ("家里的门锁密码是 3721", 1),
    ("我电脑的登录密码是 8848", 1),
    ("下个月 15 号是截止日期", 2),
    ("家里的住址是城东花园 3 栋", 1),
    ("银行卡密码是六位数", 2),
    ("上周二午饭吃的是番茄炒蛋", 0),
    ("前天那个路人的衣服是深蓝色的", 0),
    ("三个月前看过一个饮料广告", 0),
    ("昨天刷到一个搞笑视频", 1),
    ("去年某天下午是阴天", 0),
]
GROUPS = {"情绪类": list(range(0, 15)), "硬事实": list(range(15, 20)),
          "日常无关": list(range(20, 25))}


def pearson(a, b):
    n = len(a); ma = sum(a)/n; mb = sum(b)/n
    va = sum((x-ma)**2 for x in a); vb = sum((x-mb)**2 for x in b)
    if va == 0 or vb == 0: return 0.0
    return sum((a[i]-ma)*(b[i]-mb) for i in range(n))/((va*vb)**.5)


def spearman(a, b):
    def rk(v):
        o = sorted(range(len(v)), key=lambda i: v[i]); r=[0.0]*len(v); i=0
        while i < len(o):
            j=i
            while j+1<len(o) and v[o[j+1]]==v[o[i]]: j+=1
            avg=(i+j)/2+1
            for k in range(i,j+1): r[o[k]]=avg
            i=j+1
        return r
    return pearson(rk(a), rk(b))


def main():
    import numpy as np
    import torch
    from transformers import AutoTokenizer, AutoModel
    import affect_v2 as av
    torch.set_num_threads(4)

    tok = AutoTokenizer.from_pretrained(MODEL_DIR)
    mdl = AutoModel.from_pretrained(MODEL_DIR).eval()
    def enc(texts):
        if isinstance(texts, str): texts=[texts]
        with torch.no_grad():
            e = tok(texts, padding=True, truncation=True, max_length=128,
                    return_tensors="pt")
            o = mdl(**e)
        v = o.last_hidden_state[:,0].numpy().astype("float32")
        n = np.linalg.norm(v,axis=1,keepdims=True); n[n==0]=1
        return v/n

    texts=[t for t,_ in EVAL]; gold=[g for _,g in EVAL]

    Vh=enc(av.PROTO_HIGH); Vl=enc(av.PROTO_LOW)
    ch=Vh.mean(0); ch/=np.linalg.norm(ch)
    cl=Vl.mean(0); cl/=np.linalg.norm(cl)
    V=enc(texts)
    d = (V@ch) - (V@cl)
    d_hi = (Vh@ch)-(Vh@cl)
    d_lo = (Vl@ch)-(Vl@cl)
    c = float((d_hi.mean()+d_lo.mean())/2)
    T = float(max(abs(d_hi.mean()-d_lo.mean())/4.0, 1e-3))
    sd_lo = float(d_lo.std() if d_lo.std()>1e-6 else 1e-3)

    cals = {
        "CAL-A minmax":   (d - d.min()) / (d.max() - d.min()) * 10,
        "CAL-B sigmoid":  10 / (1 + np.exp(-np.clip((d - c) / T, -30, 30))),
        "CAL-C zscore":   10 / (1 + np.exp(-np.clip((d - d_lo.mean()) / sd_lo / 2, -30, 30))),
    }

    print("=" * 92)
    print("E-65 原型锚定校准方案对比")
    print("=" * 92)
    print(f"\n{c} = {c:+.4f}   T = {T:.4f}   sd_lo = {sd_lo:.4f}\n")

    print("-" * 92)
    print(f"{'方案':<16}{'Pearson':<12}{'Spearman':<12}{'情绪类':<10}"
          f"{'硬事实':<10}{'日常':<10}{'日常虚高'}")
    print("-" * 92)
    res={}
    for name, arr in cals.items():
        arr = np.asarray(arr, float)
        p, s = pearson(arr.tolist(), gold), spearman(arr.tolist(), gold)
        g = {k: float(arr[v].mean()) for k, v in GROUPS.items()}
        over = g["日常无关"] - 0.0        # 人工日常组≈0.2
        res[name] = {"pearson": round(p,4), "spearman": round(s,4),
                     "groups": {k: round(v,2) for k,v in g.items()},
                     "daily_mean": round(g["日常无关"],2)}
        print(f"{name:<16}{p:<+12.3f}{s:<+12.3f}{g['情绪类']:<10.2f}"
              f"{g['硬事实']:<10.2f}{g['日常无关']:<10.2f}{over:<+10.2f}")
    print("-" * 92)
    print(f"  人工基准：情绪类 6.53  硬事实 1.40  日常 0.20")

    # 逐条看 CAL-B
    print("\n" + "-" * 92)
    print("CAL-B 逐条（看是否区分开）")
    print("-" * 92)
    arrB = cals["CAL-B sigmoid"]
    print(f"{'文本':<38}{'人工T':<8}{'CAL-B':<8}{'判定'}")
    print("-" * 92)
    for i,(t,g) in enumerate(EVAL):
        cat = "情绪" if i<15 else ("硬事实" if i<20 else "日常")
        flag = ""
        if cat=="日常" and arrB[i] > 3.5: flag=" ⚠虚高"
        if cat=="情绪" and arrB[i] < 3.5: flag=" ⚠漏判"
        print(f"{t[:36]:<38}{g:<8}{arrB[i]:<8.2f}{cat}{flag}")
    print("-" * 92)

    # 结论
    print("\n" + "=" * 92)
    print("结论")
    print("=" * 92)
    best = max(res.items(), key=lambda kv: kv[1]["pearson"])
    print(f"  最好方案: {best[0]}  ρ={best[1]['pearson']:+.3f}")
    for n,r in res.items():
        d_hi_err = abs(r["groups"]["情绪类"] - 6.53)
        d_lo_err = abs(r["groups"]["日常无关"] - 0.20)
        print(f"    {n:<16} ρ={r['pearson']:+.3f}  "
              f"情绪组误差={d_hi_err:.2f}  日常组误差={d_lo_err:.2f}")
    print(f"\n  判读：好的方案应同时满足 —— 情绪组高、日常组低、ρ高")
    # 综合评分
    score = {}
    for n,r in res.items():
        score[n] = r["pearson"] - 0.05*abs(r["groups"]["日常无关"]-0.2)
    best2 = max(score.items(), key=lambda kv: kv[1])
    print(f"  综合最佳: {best2[0]}")

    out={"experiment":"E-65","n":len(EVAL),
         "calib":{"c":round(c,4),"T":round(T,4),"sd_lo":round(sd_lo,4)},
         "results":res,"best":best2[0],
         "raw":{"gold":gold,"CAL_B":arrB.tolist()}}
    with open(os.path.join(RESULTS,"e65_calib.json"),"w",encoding="utf-8") as f:
        json.dump(out,f,ensure_ascii=False,indent=1)
    print(f"\n写入 {os.path.join(RESULTS,'e65_calib.json')}")


if __name__ == "__main__":
    main()
