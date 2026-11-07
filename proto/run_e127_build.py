"""
E-127 真模型端到端验证（不用回放替身）

E-122 里模型判断 14/14，但那是在独立任务包上跑的一次判断。
E-123 的 12/12 用的是 gold 回放，只验证系统链路。

本实验做真验证：
  · 用独立测试库（不碰 dsh_mem.db）
  · 每条用例走完整链路：系统打包 → 真模型判断 → 系统落盘
  · 模型由外部（子代理/插件通道）判断，结果写回 JSON
"""
from __future__ import annotations

import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, "cosmos_mem"))
TASK = os.path.join(_HERE, "work", "e127")
os.makedirs(TASK, exist_ok=True)
RESULTS = os.path.join(_HERE, "results")

# 用例：基础记忆 + 新记忆 + 期望
BASE = [
    "我电脑的登录密码是 8848",
    "家里的门锁密码是 3721",
    "这个项目 15 号截止",
    "我女儿在城南路18号上中班",
    "我的车是白色卡罗拉",
    "我住在城东花园3栋",
    "接口参数定的是0.85",
    "明天下午三点开会",
    "客户的验收标准是延迟低于5ms",
    "客户要求界面必须支持深色模式",
    "老王说门锁密码是 3721",
    "我要连续吃两周的药",
]

CASES = [
    ("我把电脑密码改成了9988", "我电脑的登录密码是 8848", "replace"),
    ("门锁密码换成了9512", "家里的门锁密码是 3721", "replace"),
    ("客户同意把截止延到20号", "这个项目 15 号截止", "replace"),
    ("我换车了现在是黑色大众", "我的车是白色卡罗拉", "replace"),
    ("我搬家到城西新村5号", "我住在城东花园3栋", "replace"),
    ("接口参数改成了1.2", "接口参数定的是0.85", "replace"),
    ("会议改到明天上午十点", "明天下午三点开会", "replace"),
    ("客户还要求界面必须支持大字体", "客户要求界面必须支持深色模式", "merge"),
    ("另外我晚上也要吃一次药", "我要连续吃两周的药", "merge"),
    ("小李说门锁密码是9512", "老王说门锁密码是 3721", "conflict"),
    ("我记得好像是18号截止", "这个项目 15 号截止", "conflict"),
    ("这个bug反复出现我快崩溃了", None, "none"),
    ("上周二午饭吃的是番茄炒蛋", None, "none"),
    ("我妈打电话说家里都好", None, "none"),
]


def build_packs():
    """阶段一：建库、打包，把候选包写到文件供模型判断"""
    from store_v2 import MemoryStore
    from store_v1 import Embedder
    import conflict

    DB = os.path.join(TASK, "e127.db")
    for suf in ("", "-wal", "-shm"):
        p = DB + suf
        if os.path.exists(p):
            os.remove(p)

    st = MemoryStore(DB)
    emb = Embedder.get()

    ids = {}
    for t in BASE:
        ep = st.add(t, resolve_conflict=False)
        ids[t] = ep.ep_id

    packs = []
    for i, (new, target, expect) in enumerate(CASES):
        # 关键：被 replace 的目标可能已被前面的用例标失效，
        # 为保证独立性，每个用例重新建库
        pass

    # 改为每个用例独立库（避免状态依赖，E-123 的教训）
    out = []
    for i, (new, target, expect) in enumerate(CASES):
        db_i = os.path.join(TASK, f"e127_{i}.db")
        for suf in ("", "-wal", "-shm"):
            p = db_i + suf
            if os.path.exists(p):
                os.remove(p)
        st_i = MemoryStore(db_i)
        ids_i = {}
        for t in BASE:
            ep = st_i.add(t, resolve_conflict=False)
            ids_i[t] = ep.ep_id

        pack = conflict.prepare_candidates(st_i, emb, new, top_k=8)

        # 关键：把新记忆也写进库，否则后续落盘验证找不到它
        # （E-127 第一版漏了这一步，导致落盘 0/7）
        # resolve_conflict=False：判定由模型做，不由系统做
        ep_new = st_i.add(new, resolve_conflict=False)

        out.append({
            "cid": f"S{i:02d}",
            "new_memory": new,
            "new_ep_id": ep_new.ep_id,
            "new_value_types": pack.new_value_types,
            "new_value_tokens": pack.new_value_tokens,
            "candidates": [
                {"text": c.text, "cos": c.cos,
                 "value_types": c.value_types,
                 "value_tokens": c.value_tokens}
                for c in pack.candidates
            ],
            "gold_relation": expect,
            "gold_target": target,
            "db": db_i,
            "target_ep_id": ids_i.get(target) if target else None,
        })
        st_i.con.close()

    with open(os.path.join(TASK, "packs.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)

    print(f"已生成 {len(out)} 个候选包 → {TASK}/packs.json")
    for item in out[:3]:
        print(f"\n[{item['cid']}] {item['new_memory']}")
        print(f"  值类型 {item['new_value_types']} 值 {item['new_value_tokens']}")
        for c in item["candidates"][:3]:
            tgt = " ←gold目标" if c["text"] == item["gold_target"] else ""
            print(f"    cos={c['cos']:.4f} {c['value_types']} {c['text'][:36]}{tgt}")


if __name__ == "__main__":
    build_packs()
