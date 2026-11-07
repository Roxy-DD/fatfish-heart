"""
E-149 情绪通道诊断：affect 是否严重虚高

真实库实测（127 条）：
  affect>=0.15 只有 12 条（9.4%），但其中多条明显不该有情绪：
    「我电脑的登录密码是 8848」       aff=0.90
    「用户明确要求不可变更的底线…」    aff=0.70
    「性能优化累计约5倍…」            aff=0.45

这提示 affect_v3 的显式词法在技术/规则类文本上误判。

本实验：
  ① 用人工标注的"真实情绪强度"对照 affect_v3 的输出
  ② 找出误判的具体词
  ③ 判断是该修规则还是该换模型判断
"""
from __future__ import annotations

import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, "cosmos_mem"))
RESULTS = os.path.join(_HERE, "results")

# 人工标注：文本 + 真实情绪强度(0-1) + 类型
# 情绪判断标准：这句话描述的事件本身是否有情绪冲击
SAMPLES = [
    # ---- 应为 0：纯技术/规则/流程 ----
    ("我电脑的登录密码是 8848", 0.0, "技术"),
    ("接口参数定的是0.85", 0.0, "技术"),
    ("服务器部署在阿里云华东区", 0.0, "技术"),
    ("数据库用的是PostgreSQL 15", 0.0, "技术"),
    ("缓存层用的是Redis 7", 0.0, "技术"),
    ("性能优化累计约5倍：端到端延迟从 21.2ms 降到约 4.2ms", 0.0, "技术"),
    ("用户明确要求不可变更的底线：迁移要百分百无损", 0.0, "规则"),
    ("红线R1：记忆必须能无损迁移，跨进程重载后逐字段一致", 0.0, "规则"),
    ("团队要求接口文档必须在开发前完成", 0.0, "规则"),
    ("客户要求所有接口响应时间低于200ms", 0.0, "规则"),
    ("这个项目 15 号截止", 0.0, "流程"),
    ("明天下午三点开会", 0.0, "流程"),
    ("单元测试由李娜负责", 0.0, "流程"),

    # ---- 应为中高：真实情绪事件 ----
    ("爷爷去世了，那阵子我一直在医院和家之间来回跑", 0.92, "丧亲"),
    ("这个千万别搞错，搞错了要出大事", 0.85, "警示"),
    ("孩子第一次叫妈妈，我眼泪都出来了", 0.90, "喜悦"),
    ("上个月忘带钥匙被锁在门外，在楼道坐了两个小时", 0.60, "挫败"),
    ("住院那半个月我每天只睡四小时，人都瘦脱相了", 0.80, "疲惫"),
    ("小时候夏天傍晚在院子里吃饭，天还很亮", 0.45, "温馨"),
    ("外婆坐在竹椅上给我扇扇子，说天黑了就去睡觉", 0.55, "温馨"),
    ("我爸去年冬天做了心脏支架手术", 0.75, "担忧"),
    ("我初恋叫小雨，是高中同班", 0.50, "怀念"),
    ("那天吵架以后我一个人在江边坐到半夜", 0.85, "痛苦"),
    ("同事在所有人面前说我做的东西是垃圾", 0.88, "羞辱"),
    ("拿到offer那一刻我手都在抖", 0.90, "兴奋"),
]


def main():
    from affect_v3 import estimate_affect_v3
    import affect_v3

    print("=" * 96)
    print("E-149 情绪通道诊断：affect 是否虚高")
    print("=" * 96)

    print(f"\n{'标注类型':<10}{'真实':<8}{'v3 估计':<10}{'偏差':<10}{'文本'}")
    print("-" * 96)

    rows = []
    for text, gold, kind in SAMPLES:
        r = estimate_affect_v3(text, explicit=False)
        pred = float(r.get("affect_word") or 0)
        diff = pred - gold
        rows.append({"text": text, "kind": kind, "gold": gold,
                     "pred": round(pred, 3), "diff": round(diff, 3)})
        flag = ""
        if abs(diff) >= 0.3:
            flag = " ⚠"
        print(f"{kind:<10}{gold:<8.2f}{pred:<10.2f}{diff:<+10.2f}{text[:44]}{flag}")

    # ---------- 分组统计 ----------
    print("\n" + "=" * 96)
    print("分组统计")
    print("=" * 96)

    tech = [r for r in rows if r["kind"] in ("技术", "规则", "流程")]
    emo = [r for r in rows if r["kind"] not in ("技术", "规则", "流程")]

    def stat(rs, name):
        if not rs:
            return
        over = sum(1 for r in rs if r["pred"] > r["gold"] + 0.2)
        under = sum(1 for r in rs if r["pred"] < r["gold"] - 0.2)
        mae = sum(abs(r["diff"]) for r in rs) / len(rs)
        print(f"\n  {name}（{len(rs)} 条）:")
        print(f"    平均绝对误差 {mae:.3f}")
        print(f"    虚高（预测超标注 0.2 以上）: {over} 条 ({over/len(rs):.0%})")
        print(f"    低估: {under} 条 ({under/len(rs):.0%})")

    stat(tech, "技术/规则/流程类（真实情绪≈0）")
    stat(emo, "真实情绪事件类")

    # ---------- 关键：无情绪组是否虚高 ----------
    print("\n" + "-" * 96)
    print("关键检验：真实情绪≈0 的组，是否被误判为有情绪")
    print("-" * 96)
    false_pos = [r for r in tech if r["pred"] >= 0.3]
    print(f"\n  虚高到 >=0.3 的有 {len(false_pos)}/{len(tech)} 条:")
    for r in false_pos:
        print(f"    pred={r['pred']:.2f}  {r['text'][:60]}")

    # ---------- 找误判词 ----------
    print("\n" + "-" * 96)
    print("误判词排查")
    print("-" * 96)
    lexicons = {}
    for name in ("EMOTION_WORDS", "EVENT_EMOTION", "STRONG_WORDS",
                 "WEAK_WORDS", "SCENE_WORDS"):
        if hasattr(affect_v3, name):
            lexicons[name] = getattr(affect_v3, name)

    for r in false_pos:
        t = r["text"]
        hits = []
        for lname, lex in lexicons.items():
            if isinstance(lex, dict):
                for w in lex:
                    if w in t:
                        hits.append(f"{lname}:{w}")
            elif isinstance(lex, (list, tuple, set)):
                for w in lex:
                    if w in t:
                        hits.append(f"{lname}:{w}")
        if hits:
            print(f"  {t[:46]}")
            print(f"    → 命中: {', '.join(hits[:6])}")

    # ---------- 结论 ----------
    print("\n" + "=" * 96)
    print("结论")
    print("=" * 96)

    tech_mae = sum(abs(r["diff"]) for r in tech) / len(tech)
    emo_mae = sum(abs(r["diff"]) for r in emo) / len(emo)
    n_fp = len(false_pos)

    print(f"\n  技术/规则类平均误差: {tech_mae:.3f}")
    print(f"  情绪事件类平均误差:   {emo_mae:.3f}")
    print(f"  虚高 >=0.3 的条数:    {n_fp}/{len(tech)}")

    print(f"\n  判断:")
    if n_fp >= 3:
        print(f"    → ❌ 显式词法在技术/规则类文本上系统性虚高")
        print(f"    → 这会让「情绪共振」误触发：两条技术记忆被当成情绪相似")
        print(f"    → 需要改：规则收窄 或 交给模型判断")
    elif tech_mae < 0.2:
        print(f"    → ✅ 无情绪组基本准确（误差 {tech_mae:.3f}）")
        print(f"    → 情绪通道的问题不在虚高，而在覆盖（只有 9.4% 有情绪）")
    else:
        print(f"    → 有虚高但不严重，需进一步观察")

    out = {"experiment": "E-149",
           "tech_mae": round(tech_mae, 4),
           "emo_mae": round(emo_mae, 4),
           "false_positive": n_fp,
           "tech_count": len(tech),
           "rows": rows}
    with open(os.path.join(RESULTS, "e149_affect_diag.json"), "w",
              encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(f"\n写入 {os.path.join(RESULTS, 'e149_affect_diag.json')}")


if __name__ == "__main__":
    main()
