"""
E-140 duplicate 与 merge 的区分能力（带负例）

E-139b 的缺陷（模型自己指出来的）：
  8/8 全判 duplicate，没有 merge/replace/none 的负例
  → 只能证明"能识别重复"，不能证明"能区分重复 vs 有增益"

本实验设计配对样本：
  同样高度相似的一对记忆，但一组是 duplicate（零增益），一组是 merge（有增益）
  只有模型能区分这两者，duplicate 才有意义。

设计 12 组，每组都是"高度相似但有微妙差异"：
  · duplicate：差异只是措辞，信息量相同
  · merge：差异是实质信息增益
  两类在余弦上几乎一样高（0.95+），只能靠语义区分。
"""
from __future__ import annotations

import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, "cosmos_mem"))
TASK = os.path.join(_HERE, "work", "e140")
os.makedirs(TASK, exist_ok=True)
RESULTS = os.path.join(_HERE, "results")

# (旧记忆, 新记忆, 期望关系, 类型)
CASES = [
    # ---------- duplicate 组：只改措辞 ----------
    ("用户要求所有结论以实验数据为准，不接受没有数据支撑的表述",
     "用户要求全部结论以实验数据为准，不接受缺乏数据支撑的表述",
     "duplicate", "同义替换"),
    ("我电脑的登录密码是 8848",
     "我电脑的登录密码为 8848",
     "duplicate", "同义替换"),
    ("这个项目 15 号截止",
     "该项目 15 号截止",
     "duplicate", "同义替换"),
    ("我住在城东花园3栋",
     "我住在城东花园三栋",
     "duplicate", "数字写法"),
    ("客户要求界面必须支持深色模式",
     "客户要求界面一定要支持深色模式",
     "duplicate", "同义替换"),
    ("明天下午三点开会",
     "明天下午3点开会",
     "duplicate", "数字写法"),

    # ---------- merge 组：有实质信息增益 ----------
    ("客户要求界面必须支持深色模式",
     "客户要求界面必须支持深色模式，并且要大字体",
     "merge", "新增要求"),
    ("我电脑的登录密码是 8848",
     "我电脑的登录密码是 8848，管理员密码是 1234",
     "merge", "新增信息"),
    ("这个项目 15 号截止",
     "这个项目 15 号截止，但客户说可以宽限三天",
     "merge", "新增条件"),
    ("我住在城东花园3栋",
     "我住在城东花园3栋，502 室",
     "merge", "新增房间号"),
    ("客户要求所有接口响应时间低于200ms",
     "客户要求所有接口响应时间低于200ms，并且要支持并发1000",
     "merge", "新增指标"),
    ("我要连续吃两周的药",
     "我要连续吃两周的药，每天早上饭后一次",
     "merge", "新增用法"),

    # ---------- replace 对照组 ----------
    ("我电脑的登录密码是 8848",
     "我把电脑登录密码改成了 9988",
     "replace", "换值"),
    ("这个项目 15 号截止",
     "客户同意把截止延到 20 号",
     "replace", "延期"),

    # ---------- none 对照组 ----------
    ("我电脑的登录密码是 8848",
     "今天中午吃了牛肉面",
     "none", "无关"),
    ("客户要求界面必须支持深色模式",
     "上周六看了场电影",
     "none", "无关"),
]


def main():
    from store_v2 import MemoryStore
    from store_v1 import Embedder
    import conflict

    db = os.path.join(TASK, "e140.db")
    for suf in ("", "-wal", "-shm"):
        p = db + suf
        if os.path.exists(p):
            os.remove(p)

    st = MemoryStore(db)
    emb = Embedder.get()

    # 写入所有"旧记忆"
    olds = list({c[0] for c in CASES})
    for o in olds:
        st.add(o, resolve_conflict=False)

    print("=" * 94)
    print("E-140 duplicate vs merge 区分能力")
    print("=" * 94)
    print(f"\n库中 {len(olds)} 条旧记忆，{len(CASES)} 组对照\n")

    # 每组打包
    packs = []
    print(f"{'期望':<12}{'类型':<12}{'cos':<9}{'新记忆'}")
    print("-" * 94)
    for i, (old, new, expect, kind) in enumerate(CASES):
        pack = conflict.prepare_candidates(st, emb, new, top_k=20)
        top = next((c for c in pack.candidates if c.text == old), None)
        cos = top.cos if top else 0
        print(f"{expect:<12}{kind:<12}{cos:<9.4f}{new[:44]}")

        packs.append({
            "cid": f"E{i:02d}",
            "expect": expect,
            "kind": kind,
            "new_memory": new,
            "candidates": [
                {"text": c.text, "cos": c.cos,
                 "value_types": c.value_types,
                 "value_tokens": c.value_tokens}
                for c in pack.candidates
            ],
        })

    st.con.close()

    with open(os.path.join(TASK, "packs.json"), "w", encoding="utf-8") as f:
        json.dump(packs, f, ensure_ascii=False, indent=1)

    print("\n" + "=" * 94)
    print(f"任务包 → {TASK}/packs.json（{len(packs)} 组）")
    print(f"\n关键：duplicate 组的差异只是措辞，merge 组的差异是实质增益，")
    print(f"      两者余弦都 >0.9，只能靠语义区分 → 这正是要验证的能力。")


if __name__ == "__main__":
    main()
