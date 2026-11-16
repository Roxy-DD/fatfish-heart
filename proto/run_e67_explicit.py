"""
E-67 验证 affect_v3（显式词为主）能否修好"日常虚高"

对照同一评测集（25 句），比较四种 affect 估计：
  E-65 两原型（语义锚定）
  E-66 三原型减flat（语义锚定改进）
  **V3-affect_word（显式情绪词）**
  **V3-scene_specific（场景具体度）**

关键判据：哪个量能真正分开"情绪类"与"日常无关"。
"""
from __future__ import annotations

import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, "cosmos_mem"))
RESULTS = os.path.join(_HERE, "results")
MODEL_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "models", "bge-small-zh")

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
# 额外补 10 句"显式带情绪词"的，检验 V3 在显式场景下是否准
EVAL2 = [
    ("这个千万别搞错，搞错了要出大事", 9),
    ("项目上线成功了，大家都特别开心", 8),
    ("我被裁了，那天晚上一个人在车里坐了很久", 9),
    ("她说了那句话，我特别难受", 7),
    ("终于拿到 offer 了，我激动得跳起来", 8),
    ("这个 bug 反复出现，我快崩溃了", 8),
    ("孩子第一次叫妈妈，我眼泪一下就出来了", 9),
    ("体检报告有问题，我有点担心", 5),
    ("这次合作挺顺利的，我挺满意", 4),
    ("钱包丢了，里面有身份证和所有的卡", 8),
]
GROUPS = {"情绪类": list(range(0, 15)), "硬事实": list(range(15, 20)),
          "日常无关": list(range(20, 25))}


def pearson(a, b):
    n=len(a); ma=sum(a)/n; mb=sum(b)/n
    va=sum((x-ma)**2 for x in a); vb=sum((x-mb)**2 for x in b)
    if va==0 or vb==0: return 0.0
    return sum((a[i]-ma)*(b[i]-mb) for i in range(n))/((va*vb)**.5)


def spearman(a,b):
    def rk(v):
        o=sorted(range(len(v)),key=lambda i:v[i]); r=[0.0]*len(v); i=0
        while i<len(o):
            j=i
            while j+1<len(o) and v[o[j+1]]==v[o[i]]: j+=1
            avg=(i+j)/2+1
            for k in range(i,j+1): r[o[k]]=avg
            i=j+1
        return r
    return pearson(rk(a),rk(b))


def main():
    import numpy as np
    import torch
    from transformers import AutoTokenizer, AutoModel
    from affect_v3 import affect_word_score, scene_specific_score
    torch.set_num_threads(4)

    tok=AutoTokenizer.from_pretrained(MODEL_DIR)
    mdl=AutoModel.from_pretrained(MODEL_DIR).eval()
    def enc(ts):
        if isinstance(ts,str): ts=[ts]
        with torch.no_grad():
            e=tok(ts,padding=True,truncation=True,max_length=128,return_tensors="pt")
            o=mdl(**e)
        v=o.last_hidden_state[:,0].numpy().astype("float32")
        n=np.linalg.norm(v,axis=1,keepdims=True); n[n==0]=1
        return v/n

    all_eval = EVAL + EVAL2
    texts=[t for t,_ in all_eval]; gold=[g for _,g in all_eval]

    # 语义两原型（E-65）
    from affect_v2 import PROTO_HIGH, PROTO_LOW
    Vh,Vl=enc(PROTO_HIGH),enc(PROTO_LOW)
    ch=Vh.mean(0); ch/=np.linalg.norm(ch); cl=Vl.mean(0); cl/=np.linalg.norm(cl)
    V=enc(texts)
    d=(V@ch)-(V@cl)
    d_hi=(Vh@ch)-(Vh@cl); d_lo=(Vl@ch)-(Vl@cl)
    c=float((d_hi.mean()+d_lo.mean())/2); T=float(max(abs(d_hi.mean()-d_lo.mean())/4,1e-3))
    sem = 10/(1+np.exp(-np.clip((d-c)/T,-30,30)))

    # V3 两个量
    aw=[]; ss=[]
    for t in texts:
        a,_=affect_word_score(t)
        aw.append(a*10); ss.append(scene_specific_score(t)*10)

    methods={"语义两原型":sem, "V3 affect_word":np.array(aw),
             "V3 scene_specific":np.array(ss)}

    print("=" * 96)
    print("E-67 显式词 vs 语义锚定")
    print("=" * 96)
    print(f"\n评测 {len(all_eval)} 句（25 原集 + 10 显式情绪句）\n")

    print("-" * 96)
    print(f"{'方法':<20}{'Pearson':<11}{'Spearman':<11}{'情绪类':<9}"
          f"{'硬事实':<9}{'日常':<9}{'日常误差'}")
    print("-" * 96)
    res={}
    for name,arr in methods.items():
        arr=np.asarray(arr,float)
        p,s=pearson(arr.tolist(),gold),spearman(arr.tolist(),gold)
        g={k:float(arr[v].mean()) for k,v in GROUPS.items()}
        res[name]={"pearson":round(p,4),"spearman":round(s,4),
                   "groups":{k:round(v,2) for k,v in g.items()},
                   "daily_error":round(abs(g["日常无关"]-0.2),2)}
        print(f"{name:<20}{p:<+11.3f}{s:<+11.3f}{g['情绪类']:<9.2f}"
              f"{g['硬事实']:<9.2f}{g['日常无关']:<9.2f}"
              f"{abs(g['日常无关']-0.2):<+9.2f}")
    print("-" * 96)
    g0={'情绪类':sum(gold[:15])/15,'硬事实':sum(gold[15:20])/5,
        '日常无关':sum(gold[20:25])/5}
    print(f"{'人工基准':<20}{'':<11}{'':<11}{g0['情绪类']:<9.2f}"
          f"{g0['硬事实']:<9.2f}{g0['日常无关']:<9.2f}")

    # 显式情绪句单独看
    print("\n" + "-" * 96)
    print("新加的 10 句显式情绪句（检验 V3 在显式场景下是否准）")
    print("-" * 96)
    print(f"{'文本':<36}{'人工T':<8}{'语义':<9}{'affect_word':<13}{'scene':<8}")
    print("-" * 96)
    for i in range(25, 35):
        print(f"{all_eval[i][0][:34]:<36}{all_eval[i][1]:<8}"
              f"{sem[i]:<9.2f}{aw[i]:<13.2f}{ss[i]:<8.2f}")
    print("-" * 96)

    # 三句虚高
    print("\n三个「日常虚高」句")
    print("-" * 96)
    print(f"{'文本':<36}{'人工T':<8}{'语义':<9}{'affect_word':<13}{'scene':<8}")
    print("-" * 96)
    for i in [21,23,20]:
        print(f"{all_eval[i][0][:34]:<36}{all_eval[i][1]:<8}"
              f"{sem[i]:<9.2f}{aw[i]:<13.2f}{ss[i]:<8.2f}")
    print("-" * 96)
    print("  注：scene_specific 高是正常的——这些句子确实很具体。")
    print("      它表示'有现场可重新进入'，不表示'有情绪'。")

    # 结论
    print("\n" + "=" * 96)
    print("结论")
    print("=" * 96)
    for n,r in res.items():
        print(f"  {n:<20} ρ={r['pearson']:+.3f}  日常误差={r['daily_error']:.2f}")
    print("\n  判读：")
    aw_r = res["V3 affect_word"]
    sem_r = res["语义两原型"]
    if aw_r["daily_error"] < sem_r["daily_error"] - 1.0:
        print(f"  ✔ affect_word 的日常误差 {aw_r['daily_error']:.2f} "
              f"显著低于语义 {sem_r['daily_error']:.2f}")
        print(f"    → 显式词法在抑制虚高上优于语义锚定")
    else:
        print(f"  ✘ 显式词法没有明显改善")

    out={"experiment":"E-67","n":len(all_eval),"results":res,
         "raw":{"gold":gold,"sem":sem.tolist(),"aw":aw,"ss":ss}}
    with open(os.path.join(RESULTS,"e67_explicit.json"),"w",encoding="utf-8") as f:
        json.dump(out,f,ensure_ascii=False,indent=1)
    print(f"\n写入 {os.path.join(RESULTS,'e67_explicit.json')}")


if __name__ == "__main__":
    main()
