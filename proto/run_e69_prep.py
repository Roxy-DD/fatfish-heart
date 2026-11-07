"""
E-69 验证"模型能自己判断回忆对不对"这个前提

用户的洞察（2026-10-26）：
  "让模型在存储记忆之后，自己尝试去检查一遍……记忆完之后马上回忆一下，
   看看到底有没有正常回忆出来。然后就可以让模型自己去修改回忆的各种权重。
   就相当于是给了模型一个桶，让它自己去装，让它自己看用起来到底舒不舒服，
   不舒服自己去改。"
  "模型自己可以知道它有没有正常回忆，也就是说模型自己具有这种能力。
   但是它的记忆系统可能会限制它的能力。"

这个闭环能否成立，取决于一个可测前提：
  **给定「一个问题」和「若干条候选记忆」，模型能否可靠判断哪条真的能回答问题？**

本实验直接测这个前提。

设计：
  1. 从语料里取 20 个 (问题, 正确记忆) 对
  2. 对每个问题，检索 top-5（含正确 + 干扰）
  3. 打乱后交给**独立模型实例**，让它逐条判断"这条能不能回答这个问题"
  4. 与客观真值比对，算准确率/精确率/召回率/F1

若模型判断 F1 >= 0.85 → 闭环可行，可用来自动调权
若很低 → 说明模型的自我判断也不可靠，这个思路要换
"""
from __future__ import annotations

import json
import os
import random
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, "cosmos_mem"))
RESULTS = os.path.join(_HERE, "results")
TASK = os.path.join(_HERE, "work", "e69_task")
os.makedirs(TASK, exist_ok=True)
os.environ.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")

# 语料：每条带一个可回答它的"自然问法"
CORPUS = [
    ("我电脑的登录密码是 8848", "我电脑密码是多少"),
    ("家里的门锁密码是 3721", "家门密码是什么"),
    ("下个月 15 号是项目的截止日期，逾期不候", "项目什么时候截止"),
    ("客户明确说这条需求不能改", "客户有什么要求不能改"),
    ("这个千万别搞错，搞错了要出大事", "什么事情不能搞错"),
    ("这个 bug 反复出现，我快崩溃了", "哪个问题让我很崩溃"),
    ("小时候被邻居家的狗追着咬过，小腿上留了疤", "我小时候被狗怎么了"),
    ("第一次当众出丑，忘词站在台上，全场都看着我", "我第一次出丑是什么时候"),
    ("小时候在老家院子里看夕阳，天是橘红色的", "小时候在哪看的夕阳"),
    ("孩子第一次叫妈妈的时候", "孩子第一次叫妈妈是什么时候"),
    ("9月3日和老王讨论接口参数，最后定的是0.85", "接口参数定的多少"),
    ("上周二午饭吃的是番茄炒蛋", "上周二午饭吃什么"),
    ("前天那个路人的衣服是深蓝色的", "前天看到的那个路人穿什么颜色"),
    ("毕业典礼那天，所有人都在拍照", "毕业典礼那天在干什么"),
    ("和喜欢的人第一次约会，走过那条街", "和喜欢的人第一次约会在哪"),
    ("妈妈打电话说家里一切都好", "妈妈打电话说了什么"),
    ("这个项目的服务器地址是 192.168.1.10", "服务器地址是多少"),
    ("仓库里还有两百件库存", "库存还剩多少"),
    ("会议改到周四上午十点", "会议改到什么时候"),
    ("银行卡密码是六位数，不要告诉别人", "银行卡密码是什么"),
]


def main():
    from store_v1 import MemoryStore

    print("=" * 92)
    print("E-69 验证前提：模型能否自己判断这条记忆对不对")
    print("=" * 92)

    db = os.path.join(_HERE, "work", "e69_task", "e69.db")
    for suf in ("", "-wal", "-shm"):
        p = db + suf
        if os.path.exists(p):
            os.remove(p)

    st = MemoryStore(db)
    print("\n写入语料...")
    ids = []
    for text, _ in CORPUS:
        ep = st.add(text)
        ids.append(ep.ep_id)
    print(f"  {len(ids)} 条")

    # ---------- 构造评测包 ----------
    rng = random.Random(7)
    pack = []          # 给模型看的
    truth = {}         # 答案键（不给模型）

    print("\n检索 top-5 并打乱...")
    for i, (text, q) in enumerate(CORPUS):
        res = st.search(q, top=5)
        cands = [(ep.ep_id, ep.raw_text) for ep, *_ in res]
        # 确保正确条目在里面（若不在则强制加入，模拟"检索到但排后"）
        if ids[i] not in [c[0] for c in cands]:
            cands.append((ids[i], CORPUS[i][0]))
        rng.shuffle(cands)

        qid = f"Q{i:02d}"
        items = []
        for k, (eid, txt) in enumerate(cands):
            cid = f"{qid}-{k}"
            items.append({"cid": cid, "memory": txt})
            truth[cid] = (eid == ids[i])     # True/False
        pack.append({"qid": qid, "question": q, "candidates": items})

    with open(os.path.join(TASK, "pack.json"), "w", encoding="utf-8") as f:
        json.dump(pack, f, ensure_ascii=False, indent=1)
    with open(os.path.join(TASK, "truth.json"), "w", encoding="utf-8") as f:
        json.dump({k: bool(v) for k, v in truth.items()}, f, ensure_ascii=False, indent=1)

    n_items = sum(len(p["candidates"]) for p in pack)
    n_pos = sum(1 for v in truth.values() if v)
    print(f"\n评测包：{len(pack)} 个问题，共 {n_items} 条候选")
    print(f"  其中真正能回答的 {n_pos} 条，不能回答的 {n_items - n_pos} 条")
    print(f"  随机猜的准确率 ≈ {(n_items-n_pos)/n_items:.2f}（全说'不能'）")

    print("\n样例：")
    p0 = pack[0]
    print(f"  问题: {p0['question']}")
    for c in p0["candidates"]:
        mark = " ←正确" if truth[c["cid"]] else ""
        print(f"    [{c['cid']}] {c['memory'][:44]}{mark}")

    print(f"\n→ {TASK}")


if __name__ == "__main__":
    main()
