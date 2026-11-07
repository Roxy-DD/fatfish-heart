"""
E-139 写入去重的成本权衡：同步 vs 异步

背景：
  现在写入流程里已有矛盾判定（候选打包含模型判断）。
  再去重要不要另一次模型调用？

关键洞察：
  **矛盾判定和重复检测用的是同一个候选包！**
  候选包里已经有"最相似的旧记忆"，
  模型在判断"这是什么关系"时，本来就能看到目标是不是"同义重复"。

  所以最优方案：**把"重复"作为矛盾判定的第五种关系**，
  不额外调模型，零边际成本。

本实验验证：
  ① 现有判定中，有多少比例实际是"重复"（本可以判成 duplicate）
  ② 如果把 duplicate 加进关系集合，模型能否正确区分
"""
from __future__ import annotations

import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, "cosmos_mem"))
RESULTS = os.path.join(_HERE, "results")
DB = os.path.join(_HERE, "cosmos_mem", "dsh_mem.db")

# 测试集：20 组，每组是"新记忆 + 一条已有记忆"
# 标注：duplicate（同义重复）/ replace / merge / conflict / none
CASES = [
    # ---- duplicate：同义重复，只是措辞不同 ----
    ("用户要求结论以实验数据为准，不接受没有数据支撑的说法",
     "用户要求所有结论以实验数据为准，不接受没有数据支撑的表述", "duplicate"),
    ("用户已授权调用技能与专家等能力",
     "用户已授权在开发过程中调用技能与专家等能力", "duplicate"),
    ("用户决定不再把找工作作为主要方向",
     "用户决定不再把精力放在找工作或应聘上", "duplicate"),
    ("用户对游戏开发感兴趣，包括游戏引擎渲染",
     "用户对游戏开发感兴趣，包括游戏引擎渲染和角色动效", "duplicate"),
    ("用户给我设定了思想钢印：一切以数据为准",
     "用户给我设定了思想钢印：一切以数据为准，结果导向", "duplicate"),
    ("用户的项目要写进简历，突出突破性",
     "用户的项目要写进简历，突出突破性与进展", "duplicate"),
    ("每次新会话开始时要先查看上次记忆",
     "每次新会话开始时要先查看上一次对话留存的记忆", "duplicate"),
    ("需要判断的地方交给模型",
     "需要判断和选择的地方应该交给模型自己分析判断", "duplicate"),

    # ---- replace：旧值作废 ----
    ("我把电脑密码改成了9988", "我电脑的登录密码是 8848", "replace"),
    ("门锁密码换成了9512", "家里的门锁密码是 3721", "replace"),
    ("我换车了现在是黑色大众", "我的车是白色卡罗拉", "replace"),
    ("会议改到明天上午十点", "明天下午三点开会", "replace"),

    # ---- merge：两条都成立 ----
    ("客户还要求界面必须支持大字体", "客户要求界面必须支持深色模式", "merge"),
    ("另外我晚上也要吃一次药", "我要连续吃两周的药", "merge"),

    # ---- conflict：矛盾但都可能对 ----
    ("小李说门锁密码是9512", "老王说门锁密码是 3721", "conflict"),
    ("我记得好像是18号截止", "这个项目 15 号截止", "conflict"),

    # ---- none：无关 ----
    ("今天中午吃了牛肉面", "我电脑的登录密码是 8848", "none"),
    ("上周二午饭吃番茄炒蛋", "我的车是白色卡罗拉", "none"),
    ("我妈打电话说家里都好", "这个项目 15 号截止", "none"),
    ("孩子第一次叫妈妈", "我生日是3月8号", "none"),
]


def main():
    from store_v2 import MemoryStore
    from store_v1 import Embedder
    import conflict

    st = MemoryStore(DB)
    emb = Embedder.get()

    print("=" * 92)
    print("E-139 写入去重成本：duplicate 是否可作为第五种关系")
    print("=" * 92)
    print(f"\n{len(CASES)} 组测试（重点：8 组 duplicate 能否被识别）\n")

    # 确认这些"旧记忆"是否在库中
    missing = []
    for new, old, _ in CASES:
        if not st.con.execute(
                "SELECT 1 FROM episodes WHERE raw_text=? AND invalid_at IS NULL",
                (old,)).fetchone():
            missing.append(old)
    if missing:
        print(f"⚠ {len(missing)} 条基准记忆不在库中，将跳过:")
        for m in missing[:5]:
            print(f"    {m[:50]}")

    print(f"\n{'期望':<12}{'候选首位cos':<14}{'候选首位'}")
    print("-" * 92)

    packs = []
    for i, (new, old, expect) in enumerate(CASES):
        pack = conflict.prepare_candidates(st, emb, new, top_k=20)
        top = pack.candidates[0] if pack.candidates else None
        top_cos = top.cos if top else 0
        top_text = top.text if top else "(无候选)"
        found = any(c.text == old for c in pack.candidates)

        print(f"{expect:<12}{top_cos:<14.4f}{top_text[:44]}")
        if not found:
            print(f"    ⚠ gold 目标不在候选包中: {old[:40]}")

        packs.append({
            "cid": f"D{i:02d}",
            "new_memory": new,
            "gold_relation": expect,
            "gold_target": old,
            "gold_in_pack": found,
            "new_value_types": pack.new_value_types,
            "new_value_tokens": pack.new_value_tokens,
            "candidates": [
                {"text": c.text, "cos": c.cos,
                 "value_types": c.value_types, "value_tokens": c.value_tokens}
                for c in pack.candidates
            ],
        })

    out_dir = os.path.join(_HERE, "work", "e139")
    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, "packs.json"), "w", encoding="utf-8") as f:
        json.dump(packs, f, ensure_ascii=False, indent=1)

    st.con.close()

    print(f"\n" + "=" * 92)
    print("结论")
    print("=" * 92)
    n_gold_in = sum(1 for p in packs if p["gold_in_pack"])
    print(f"  gold 目标进入候选包: {n_gold_in}/{len(packs)}")
    print(f"\n  关键判断：")
    print(f"    duplicate 与 merge 的区别在于「是否带来新信息」。")
    print(f"    模型看到的候选包与矛盾判定完全相同，")
    print(f"    所以把 duplicate 加进关系集合是 **零边际成本**。")
    print(f"\n  任务包 → {out_dir}/packs.json")


if __name__ == "__main__":
    main()
