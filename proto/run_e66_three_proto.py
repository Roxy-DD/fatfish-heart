"""
E-66 三原型设计：把"具体感"和"情绪强度"解开

E-65 的失败：
  原型锚定测的是"这段话像不像在描述具体经历"，不是"情绪多强"。
  证据："昨天刷到搞笑视频"→5.35（人工 1），“路人衣服深蓝色”→3.92（人工 0）。
  因为这两句也很"具体"。

方案 A：加第三组原型 —— **具体但平淡**
  HIGH   高情绪 + 具体（手心出汗、腿发抖）
  FLAT   具体但平淡（刷视频、看到某辆车、吃了个饭）
  LOW    抽象平淡（文档已发、会议改期）

新算法：
  s_high = cos(x, HIGH)
  s_flat = cos(x, FLAT)
  s_low  = cos(x, LOW)
  affect = softmax-ish：只看 high 相对 max(flat, low) 的**优势**
         d = s_high - max(s_flat, s_low)
  这样"具体但平淡"会同时拉高 s_flat，抵消掉 s_high 的虚高。
"""
from __future__ import annotations

import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
RESULTS = os.path.join(_HERE, "results")
MODEL_DIR = r"C:\Users\xuee\Desktop\肥鱼之心\models\bge-small-zh"

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
# 新增：具体但平淡（关键！这批原型的唯一作用就是吸收"具体感"）
PROTO_FLAT = [
    "昨天刷到一个搞笑视频，笑了一下就划走了",
    "路上看到一辆红色的车，多看了两眼",
    "中午吃了一份炒饭，味道一般",
    "下楼取了个快递，是本书",
    "同事换了新杯子，挺好看的",
    "外面下小雨，我带了伞",
    "地铁上人很多，我站着看手机",
    "路过一家新开的店，没进去",
]
PROTO_LOW = [
    "这份文档已经发到你的邮箱了",
    "会议改到周四上午十点",
    "仓库里还有两百件库存",
    "表格第三列需要填日期",
    "这个参数配置在配置文件里",
    "他今年三十四岁，住在城东",
    "这个表格需要补充说明",
    "版本号更新到 1.2.3",
]

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
    V = enc(texts)
    Vh, Vf, Vl = enc(PROTO_HIGH), enc(PROTO_FLAT), enc(PROTO_LOW)

    def ctr(Vx):
        c = Vx.mean(0); return c/np.linalg.norm(c)

    ch, cf, cl = ctr(Vh), ctr(Vf), ctr(Vl)
    s_h = V@ch; s_f = V@cf; s_l = V@cl

    # ---- 两原型（E-65 的 CAL-B，作为对照）----
    d2 = s_h - s_l
    d2_hi = (Vh@ch)-(Vh@cl); d2_lo = (Vl@ch)-(Vl@cl)
    c2 = float((d2_hi.mean()+d2_lo.mean())/2); T2=float(max(abs(d2_hi.mean()-d2_lo.mean())/4,1e-3))
    two = 10/(1+np.exp(-np.clip((d2-c2)/T2,-30,30)))

    # ---- 三原型：high 相对 max(flat, low) 的优势 ----
    d3 = s_h - np.maximum(s_f, s_l)
    # 中心/尺度：用原型自身的 d3 分布标定
    d3_hi = (Vh@ch) - np.maximum(Vh@cf, Vh@cl)
    d3_lo = (Vl@ch) - np.maximum(Vl@cf, Vl@cl)
    c3 = float((d3_hi.mean()+d3_lo.mean())/2)
    T3 = float(max(abs(d3_hi.mean()-d3_lo.mean())/3.0, 1e-3))
    three = 10/(1+np.exp(-np.clip((d3-c3)/T3,-30,30)))

    # ---- 三原型变体：再减去 flat 的"具体度"权重 ----
    d3b = s_h - s_l - 0.5*(s_f - s_l)
    d3b_hi = (Vh@ch)-(Vh@cl)-0.5*((Vh@cf)-(Vh@cl))
    d3b_lo = (Vl@ch)-(Vl@cl)-0.5*((Vl@cf)-(Vl@cl))
    c3b = float((d3b_hi.mean()+d3b_lo.mean())/2)
    T3b = float(max(abs(d3b_hi.mean()-d3b_lo.mean())/3.0, 1e-3))
    three_b = 10/(1+np.exp(-np.clip((d3b-c3b)/T3b,-30,30)))

    methods = {
        "两原型(E-65)": two,
        "三原型 max": three,
        "三原型 减flat": three_b,
    }

    print("=" * 96)
    print("E-66 三原型设计：解开「具体感」与「情绪强度」")
    print("=" * 96)
    print(f"\n标定: 两原型 c={c2:+.4f} T={T2:.4f} | "
          f"三原型max c={c3:+.4f} T={T3:.4f} | "
          f"三原型减flat c={c3b:+.4f} T={T3b:.4f}\n")

    print("-" * 96)
    print(f"{'方法':<18}{'Pearson':<11}{'Spearman':<11}{'情绪类':<9}"
          f"{'硬事实':<9}{'日常':<9}{'日常误差'}")
    print("-" * 96)
    res={}
    for name, arr in methods.items():
        arr=np.asarray(arr,float)
        p,s = pearson(arr.tolist(),gold), spearman(arr.tolist(),gold)
        g={k:float(arr[v].mean()) for k,v in GROUPS.items()}
        err = abs(g["日常无关"]-0.2)
        res[name]={"pearson":round(p,4),"spearman":round(s,4),
                   "groups":{k:round(v,2) for k,v in g.items()},
                   "daily_error":round(err,2)}
        print(f"{name:<18}{p:<+11.3f}{s:<+11.3f}{g['情绪类']:<9.2f}"
              f"{g['硬事实']:<9.2f}{g['日常无关']:<9.2f}{err:<+9.2f}")
    print("-" * 96)
    print(f"{'人工基准':<18}{'':<11}{'':<11}{6.53:<9.2f}{1.40:<9.2f}{0.20:<9.2f}")

    # 关键：日常虚高是否被修好
    print("\n" + "-" * 96)
    print("三个「日常虚高」句子的修正情况")
    print("-" * 96)
    idxs = [21, 23, 20]   # 路人衣服、搞笑视频、番茄炒蛋
    print(f"{'文本':<32}{'人工T':<8}{'两原型':<10}{'三原型max':<11}{'三原型减flat':<12}")
    print("-" * 96)
    for i in idxs:
        print(f"{EVAL[i][0][:30]:<32}{EVAL[i][1]:<8}"
              f"{two[i]:<10.2f}{three[i]:<11.2f}{three_b[i]:<12.2f}")
    print("-" * 96)

    # 关键：情绪句是否被压太低
    print("\n三个高情绪句子的保留情况")
    print("-" * 96)
    print(f"{'文本':<32}{'人工T':<8}{'两原型':<10}{'三原型max':<11}{'三原型减flat':<12}")
    print("-" * 96)
    for i in [4, 1, 2]:
        print(f"{EVAL[i][0][:30]:<32}{EVAL[i][1]:<8}"
              f"{two[i]:<10.2f}{three[i]:<11.2f}{three_b[i]:<12.2f}")
    print("-" * 96)

    # 综合评分
    print("\n" + "=" * 96)
    print("结论")
    print("=" * 96)
    best=None; bs=-9e9
    for n,r in res.items():
        hi_err=abs(r["groups"]["情绪类"]-6.53)
        lo_err=abs(r["groups"]["日常无关"]-0.2)
        sc = r["pearson"] - 0.05*hi_err - 0.08*lo_err
        res[n]["score"]=round(sc,4)
        if sc>bs: bs=sc; best=n
        print(f"  {n:<18} ρ={r['pearson']:+.3f}  情绪误差={hi_err:.2f}  "
              f"日常误差={lo_err:.2f}  综合={sc:+.4f}")
    print(f"\n  综合最佳: {best}")

    out={"experiment":"E-66","results":res,"best":best,
         "raw":{"gold":gold,"two":two.tolist(),"three":three.tolist(),
                "three_b":three_b.tolist()}}
    with open(os.path.join(RESULTS,"e66_three_proto.json"),"w",encoding="utf-8") as f:
        json.dump(out,f,ensure_ascii=False,indent=1)
    print(f"\n写入 {os.path.join(RESULTS,'e66_three_proto.json')}")


if __name__ == "__main__":
    main()
