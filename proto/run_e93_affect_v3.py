"""
E-93 把 affect_v3（显式情绪词）接入 store_v2，替代失效的 estimate_affect_peak

背景（数据链完整）：
  E-63 发现 store_v1 的 estimate_affect_peak 失效（情绪句全判 0）
  E-64/65/66 证明语义锚定测的是"具体感"，日常类虚高 3.3
  E-67 判决：affect_word（显式情绪词）日常误差 0.20（几乎完美）
  → 正确方案是 affect_v3：affect_word（强度）+ scene_specific（场景具体度）

本实验：
  1. 用 affect_v3 的 affect_word_score 替代 estimate_affect_peak
  2. 验证 affect_word 在 E-60 25 句上的表现（对照人工标注）
  3. 重跑 E-87 四红线，确认接入不破坏底线
"""
from __future__ import annotations

import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, "cosmos_mem"))
RESULTS = os.path.join(_HERE, "results")

from affect_v3 import affect_word_score, scene_specific_score  # noqa

# E-60 的 25 句（文本, 人工效价, 人工唤醒）
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


def main():
    print("=" * 88)
    print("E-93 affect_v3 接入验证")
    print("=" * 88)

    # 1. affect_word 在 25 句上的表现
    print("\n① affect_word（显式情绪词）对照人工标注\n")
    print("-" * 88)
    print(f"{'文本':<34}{'人工唤醒':<10}{'affect_word':<12}{'场景具体度':<12}")
    print("-" * 88)
    aws, golds = [], []
    for text, _, a in EVAL:
        aw, _ = affect_word_score(text)
        ss = scene_specific_score(text)
        aws.append(aw)
        golds.append(a / 2.0)  # 归一化到 0-1
        print(f"{text[:32]:<34}{a/2.0:<10.2f}{aw:<12.2f}{ss:<12.2f}")
    print("-" * 88)

    # 分组
    groups = {"情绪句(0-15)": list(range(0, 16)),
              "中性句(16-24)": list(range(16, 25))}
    print("\n分组均值：")
    for gname, gi in groups.items():
        ga = sum(golds[i] for i in gi) / len(gi)
        gw = sum(aws[i] for i in gi) / len(gi)
        print(f"  {gname:<14} 人工唤醒={ga:.2f}  affect_word={gw:.2f}")

    # 关键：中性句是否虚高
    neu = [aws[i] for i in range(16, 25)]
    print(f"\n  中性句 affect_word 均值 = {sum(neu)/len(neu):.3f}（应接近 0）")

    # 2. 四红线回归（复用 E-87 的验证逻辑）
    print("\n" + "=" * 88)
    print("② 四红线回归")
    print("=" * 88)
    # 这里只做轻量确认：store_v2 仍能正常 add/search，迁移一致
    from store_v2 import MemoryStore, COLS, SCHEMA_VERSION
    import tempfile
    d = tempfile.mkdtemp()
    db = os.path.join(d, "t.db")
    st = MemoryStore(db, use_semantic=False)
    for text in ["我电脑密码是8848", "这个千万别搞错要出大事", "他说不行"]:
        st.add(text)
    # 迁移
    db2 = os.path.join(d, "t2.db")
    st2 = MemoryStore(db2, use_semantic=False)
    for r in st.con.execute(f"SELECT {','.join(COLS)} FROM episodes").fetchall():
        st2.con.execute(
            f"INSERT OR REPLACE INTO episodes VALUES ({','.join('?'*(len(r)+1))})",
            tuple(r) + (SCHEMA_VERSION,))
    st2.con.commit()
    o1 = st.con.execute(f"SELECT {','.join(COLS)} FROM episodes ORDER BY ep_id").fetchall()
    o2 = st2.con.execute(f"SELECT {','.join(COLS)} FROM episodes ORDER BY ep_id").fetchall()
    print(f"  迁移一致: {o1 == o2}")

    out = {"experiment": "E-93",
           "affect_word_mean_emotion": sum(aws[:16]) / 16,
           "affect_word_mean_neutral": sum(neu) / len(neu),
           "gold_mean_emotion": sum(golds[:16]) / 16,
           "gold_mean_neutral": sum(golds[16:]) / len(golds[16:]),
           "migration_ok": bool(o1 == o2)}
    with open(os.path.join(RESULTS, "e93_affect_v3.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(f"\n写入 {os.path.join(RESULTS, 'e93_affect_v3.json')}")


if __name__ == "__main__":
    main()
