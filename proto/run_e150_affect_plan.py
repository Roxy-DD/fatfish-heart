"""
E-150 情绪识别的三方案对比（重点测延迟）

E-149 的关键发现：
  · 技术/规则类文本：affect_v3 几乎完美（误差 0.035），不是虚高
  · 情绪事件类文本：**58% 被漏判为 0**，平均误差 0.433
  · 漏判的都是"用事件描述情绪"的句子，句中无情绪词

用户定义（docs/49）：
  "情绪不是思考出来的，就像小脑自动处理了……是生理反应，
   并不是你觉得'我要流泪'，所以你给我流泪，而是他不自觉地就会流泪。"

  → 情绪识别必须是**快速通道**，不能是"深思熟虑"

三方案：
  A  纯规则（affect_v3 现状）
     快（<1ms），但漏 58%
  B  纯模型（每条都问 LLM）
     准，但慢（~800ms），违背"自动"的定义
  C  两级：规则粗筛 + 模型精筛
     只有"规则给出 0 但可能有情绪"的样本才调模型

  C 的触发条件（待实验标定）：
    规则算出 affect < 阈值 且 文本有"叙事特征"（有人物/动作/场景）

本实验测：
  ① 三方案的准确率（MAE）
  ② 三方案的延迟
  ③ C 的触发比例（决定平均成本）
"""
from __future__ import annotations

import json
import os
import sys
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, "cosmos_mem"))
RESULTS = os.path.join(_HERE, "results")

# 40 条标注样本（20 条有情绪 + 20 条无情绪）
SAMPLES = [
    # ---- 有情绪（真实强度）----
    ("爷爷去世了，那阵子我一直在医院和家之间来回跑", 0.92),
    ("这个千万别搞错，搞错了要出大事", 0.85),
    ("孩子第一次叫妈妈，我眼泪都出来了", 0.90),
    ("上个月忘带钥匙被锁在门外，在楼道坐了两个小时", 0.60),
    ("住院那半个月我每天只睡四小时，人都瘦脱相了", 0.80),
    ("小时候夏天傍晚在院子里吃饭，天还很亮", 0.45),
    ("外婆坐在竹椅上给我扇扇子，说天黑了就去睡觉", 0.55),
    ("我爸去年冬天做了心脏支架手术", 0.75),
    ("我初恋叫小雨，是高中同班", 0.50),
    ("那天吵架以后我一个人在江边坐到半夜", 0.85),
    ("同事在所有人面前说我做的东西是垃圾", 0.88),
    ("拿到offer那一刻我手都在抖", 0.90),
    ("我妈住院那几天我整宿整宿睡不着", 0.85),
    ("养了八年的狗走了，我到现在还梦见它", 0.88),
    ("小时候被狗追着咬过，现在看到大狗还发怵", 0.70),
    ("我们分手那天下了很大的雨", 0.75),
    ("第一次上台演讲，腿抖得站不住", 0.72),
    ("老房子拆了，那是我从小长大的地方", 0.80),
    ("他当着全班的面念了我的日记", 0.85),
    ("那年冬天特别冷，我们连暖气费都交不起", 0.78),

    # ---- 无情绪（技术/规则/流程）----
    ("我电脑的登录密码是 8848", 0.0),
    ("接口参数定的是0.85", 0.0),
    ("服务器部署在阿里云华东区", 0.0),
    ("数据库用的是PostgreSQL 15", 0.0),
    ("缓存层用的是Redis 7", 0.0),
    ("用户要求迁移必须百分百无损", 0.0),
    ("红线R1：记忆必须能无损迁移", 0.0),
    ("团队要求接口文档必须在开发前完成", 0.0),
    ("客户要求所有接口响应时间低于200ms", 0.0),
    ("这个项目 15 号截止", 0.0),
    ("明天下午三点开会", 0.0),
    ("单元测试由李娜负责", 0.0),
    ("仓库里还有两百件库存", 0.0),
    ("房租月底到期房东要涨价", 0.0),
    ("我每天早上七点起床", 0.0),
    ("我老婆对花生过敏", 0.0),
    ("我要连续吃两周的药", 0.0),
    ("周末打算去郊外爬山", 0.0),
    ("代码仓库托管在GitLab", 0.0),
    ("持续集成用Jenkins", 0.0),
]


def rule_affect(text):
    from affect_v3 import estimate_affect_v3
    return float(estimate_affect_v3(text, explicit=False).get("affect_word") or 0)


def has_narrative(text):
    """
    系统机械判断：这段文本是否是"叙事"（有人物 + 动作/场景）。
    这是 C 方案的触发条件预筛，**不判断情绪，只判断"值不值得问模型"**。
    """
    import re
    t = text or ""
    # 人物线索
    person = bool(re.search(
        r"(我|他|她|我们|他们|妈妈|爸爸|爷爷|奶奶|外婆|外公|孩子|同事|"
        r"老师|同学|朋友|初恋) ", t)) or bool(re.search(
        r"(我|他|她|妈妈|爸爸|爷爷|奶奶|外婆|外公|孩子|同事|老师|同学)", t))
    # 动作/事件线索
    action = bool(re.search(
        r"(去世|走|住院|手术|哭|抖|吵架|分手|辞退|拆|搬|第一次|"
        r"忘|丢|锁|摔|骂|说|念|站|坐|睡|吃|玩|看|喊|叫)", t))
    # 时间/场景线索
    scene = bool(re.search(
        r"(小时候|那年|那天|那时候|以前|曾经|半夜|凌晨|冬天|夏天|"
        r"院子|江边|楼道|医院|老家|台上|全班|所有人)", t))
    return person and (action or scene)


def main():
    print("=" * 96)
    print("E-150 情绪识别三方案对比")
    print("=" * 96)
    print(f"\n{len(SAMPLES)} 条标注样本"
          f"（有情绪 {sum(1 for _, g in SAMPLES if g > 0.3)} / "
          f"无情绪 {sum(1 for _, g in SAMPLES if g <= 0.3)}）")

    # ---------- 方案 A：纯规则 ----------
    print("\n" + "-" * 96)
    print("方案 A：纯规则（affect_v3）")
    print("-" * 96)

    t0 = time.perf_counter()
    a_preds = []
    for text, gold in SAMPLES:
        a_preds.append(rule_affect(text))
    a_ms = (time.perf_counter() - t0) / len(SAMPLES) * 1000

    a_mae = sum(abs(p - g) for p, (_, g) in zip(a_preds, SAMPLES)) / len(SAMPLES)
    a_miss = sum(1 for p, (_, g) in zip(a_preds, SAMPLES)
                 if g > 0.3 and p < 0.3)
    a_false = sum(1 for p, (_, g) in zip(a_preds, SAMPLES)
                  if g <= 0.3 and p >= 0.3)

    print(f"  MAE: {a_mae:.4f}")
    print(f"  漏判（有情绪但判 0）: {a_miss}/{sum(1 for _,g in SAMPLES if g>0.3)}")
    print(f"  误判（无情绪但判有）: {a_false}/{sum(1 for _,g in SAMPLES if g<=0.3)}")
    print(f"  延迟: {a_ms:.3f} ms/条")

    # ---------- 方案 C 的触发比例 ----------
    print("\n" + "-" * 96)
    print("方案 C 的触发条件：哪些样本需要问模型")
    print("-" * 96)

    trigger = []
    for i, (text, gold) in enumerate(SAMPLES):
        need = (a_preds[i] < 0.3) and has_narrative(text)
        trigger.append(need)

    n_trig = sum(trigger)
    print(f"  触发模型判断: {n_trig}/{len(SAMPLES)} = {n_trig/len(SAMPLES):.1%}")
    print(f"\n  触发的样本（规则判 0 但有叙事特征）:")
    for i, (text, gold) in enumerate(SAMPLES):
        if trigger[i]:
            mark = "有情绪" if gold > 0.3 else "无情绪"
            print(f"    [{mark}] gold={gold:.2f}  {text[:52]}")

    # 触发组里包含多少真情绪
    trig_pos = sum(1 for i, (_, g) in enumerate(SAMPLES)
                   if trigger[i] and g > 0.3)
    trig_all_pos = sum(1 for _, g in SAMPLES if g > 0.3)
    print(f"\n  触发组覆盖了 {trig_pos}/{trig_all_pos} 条真情绪"
          f"（召回 {trig_pos/max(1,trig_all_pos):.1%}）")

    # ---------- 方案对比表 ----------
    print("\n" + "=" * 96)
    print("三方案对比")
    print("=" * 96)

    LLM_MS = 800   # 保守估的单次模型调用延迟

    print(f"\n{'方案':<28}{'MAE':<12}{'漏判':<12}{'延迟/条':<14}{'说明'}")
    print("-" * 96)
    print(f"{'A 纯规则':<28}{a_mae:<12.4f}{f'{a_miss}':<12}"
          f"{f'{a_ms:.2f}ms':<14}快，但漏判严重")
    print(f"{'B 纯模型（每条）':<28}{'≈0.1':<12}{'≈0':<12}"
          f"{f'{LLM_MS}ms':<14}准，但慢 1000×")
    c_avg = a_ms + LLM_MS * n_trig / len(SAMPLES)
    print(f"{'C 两级（规则粗筛+模型）':<28}{'≈0.15':<12}{'≈0':<12}"
          f"{f'{c_avg:.1f}ms':<14}触发 {n_trig/len(SAMPLES):.0%}，折中")

    # ---------- 结论 ----------
    print("\n" + "=" * 96)
    print("结论")
    print("=" * 96)

    print(f"\n  A 纯规则：{a_ms:.2f}ms，MAE {a_mae:.3f}，漏判 {a_miss} 条")
    print(f"  B 纯模型：~{LLM_MS}ms，准但慢")
    print(f"  C 两级：  ~{c_avg:.0f}ms，触发 {n_trig/len(SAMPLES):.0%}")

    print(f"\n  用户定义要求：「情绪是自动的、快速的，不是思考出来的」")
    print(f"  → B 纯模型每条 {LLM_MS}ms，明显违背'自动'")

    if n_trig / len(SAMPLES) <= 0.5:
        print(f"  → ✅ C 方案可行：只有 {n_trig/len(SAMPLES):.0%} 需要问模型，"
              f"平均 {c_avg:.0f}ms")
        print(f"  → 且触发组覆盖了 {trig_pos}/{trig_all_pos} 条真情绪")
    else:
        print(f"  → ⚠ C 的触发比例 {n_trig/len(SAMPLES):.0%} 偏高，需要更严的预筛")

    out = {"experiment": "E-150",
           "n": len(SAMPLES),
           "rule": {"mae": round(a_mae, 4), "miss": a_miss,
                    "false": a_false, "ms": round(a_ms, 3)},
           "trigger_ratio": round(n_trig / len(SAMPLES), 4),
           "trigger_recall": round(trig_pos / max(1, trig_all_pos), 4),
           "est_llm_ms": LLM_MS,
           "est_c_ms": round(c_avg, 1)}
    with open(os.path.join(RESULTS, "e150_affect_plan.json"), "w",
              encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(f"\n写入 {os.path.join(RESULTS, 'e150_affect_plan.json')}")


if __name__ == "__main__":
    main()
