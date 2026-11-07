"""
E-152 情绪由模型判断：写入时算，检索时读

E-149~E-151 的结论：
  · affect_v3 在技术/规则类文本上几乎完美（MAE 0.035）
  · 但在真实情绪事件上 58% 漏判为 0
  · 规则预筛（narr 特征词）最多覆盖 65%，本质是"换一个规则再来一次"

用户原则：
  "需要判断的地方，应该让模型自己来判断"
  → "这段文本有没有情绪"是判断，不是机械提取
  → 不该用规则预筛

关键成本洞察：
  情绪识别发生在**写入时**（低频，一次对话几条），不是检索时（高频）。
  所以"写入时调模型"的成本不阻塞用户。

本实验验证：
  ① 模型判断情绪的准确率（对照人工标注）
  ② 与规则法对比
  ③ 写入延迟的实际影响

用真实模型调用（子代理执行判断）。
"""
from __future__ import annotations

import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, "cosmos_mem"))
TASK = os.path.join(_HERE, "work", "e152")
os.makedirs(TASK, exist_ok=True)
RESULTS = os.path.join(_HERE, "results")

# 50 条样本：25 有情绪 + 25 无情绪，覆盖多种类型
SAMPLES = [
    # 有情绪（含规则漏判的、规则命中的）
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
    ("我妈住院那几天我整宿整宿睡不着", 0.85, "焦虑"),
    ("养了八年的狗走了，我到现在还梦见它", 0.88, "悲伤"),
    ("小时候被狗追着咬过，现在看到大狗还发怵", 0.70, "恐惧"),
    ("我们分手那天下了很大的雨", 0.75, "失落"),
    ("第一次上台演讲，腿抖得站不住", 0.72, "紧张"),
    ("老房子拆了，那是我从小长大的地方", 0.80, "怀念"),
    ("他当着全班的面念了我的日记", 0.85, "羞辱"),
    ("那年冬天特别冷，我们连暖气费都交不起", 0.78, "窘迫"),
    ("孩子发高烧到四十度，我抱着他在急诊等到天亮", 0.88, "焦急"),
    ("项目上线那天服务器崩了，我手心全是汗", 0.75, "紧张"),
    ("她笑着说没关系，但我知道她很难过", 0.70, "心疼"),
    ("收到录取通知那天我在楼道里哭了很久", 0.90, "激动"),
    ("我们把最后一件家具搬上车，那个家就空了", 0.80, "不舍"),

    # 无情绪（技术/规则/流程/事实）
    ("我电脑的登录密码是 8848", 0.0, "技术"),
    ("接口参数定的是0.85", 0.0, "技术"),
    ("服务器部署在阿里云华东区", 0.0, "技术"),
    ("数据库用的是PostgreSQL 15", 0.0, "技术"),
    ("缓存层用的是Redis 7", 0.0, "技术"),
    ("用户要求迁移必须百分百无损", 0.0, "规则"),
    ("红线R1：记忆必须能无损迁移", 0.0, "规则"),
    ("团队要求接口文档必须在开发前完成", 0.0, "规则"),
    ("客户要求所有接口响应时间低于200ms", 0.0, "规则"),
    ("这个项目 15 号截止", 0.0, "流程"),
    ("明天下午三点开会", 0.0, "流程"),
    ("单元测试由李娜负责", 0.0, "流程"),
    ("仓库里还有两百件库存", 0.0, "事实"),
    ("房租月底到期房东要涨价", 0.0, "事实"),
    ("我每天早上七点起床", 0.0, "习惯"),
    ("我老婆对花生过敏", 0.0, "事实"),
    ("我要连续吃两周的药", 0.0, "事实"),
    ("周末打算去郊外爬山", 0.0, "计划"),
    ("代码仓库托管在GitLab", 0.0, "技术"),
    ("持续集成用Jenkins", 0.0, "技术"),
    ("我昨天部署了Redis 7", 0.0, "技术"),
    ("张伟说他改完了接口", 0.0, "事实"),
    ("李娜在会议室演示了原型", 0.0, "事实"),
    ("这个函数嵌套了五层，需要重构", 0.0, "技术"),
    ("下周要去西安出差三天", 0.0, "计划"),
]


def main():
    print("=" * 96)
    print("E-152 情绪由模型判断（写入时算，检索时读）")
    print("=" * 96)
    print(f"\n{len(SAMPLES)} 条样本"
          f"（有情绪 {sum(1 for _,g,_ in SAMPLES if g>0.3)} / "
          f"无情绪 {sum(1 for _,g,_ in SAMPLES if g<=0.3)}）")

    # 写出任务包给模型
    packs = [{"cid": f"A{i:02d}", "text": t, "kind": k}
             for i, (t, g, k) in enumerate(SAMPLES)]
    with open(os.path.join(TASK, "packs.json"), "w", encoding="utf-8") as f:
        json.dump(packs, f, ensure_ascii=False, indent=1)

    # 规则法基线
    from affect_v3 import estimate_affect_v3
    rule_preds = []
    for t, g, k in SAMPLES:
        rule_preds.append(
            float(estimate_affect_v3(t, explicit=False).get("affect_word") or 0))

    rule_mae = sum(abs(p - g) for p, (_, g, _) in zip(rule_preds, SAMPLES)) / len(SAMPLES)
    n_pos = sum(1 for _, g, _ in SAMPLES if g > 0.3)
    n_neg = len(SAMPLES) - n_pos
    rule_miss = sum(1 for p, (_, g, _) in zip(rule_preds, SAMPLES)
                    if g > 0.3 and p < 0.3)
    rule_false = sum(1 for p, (_, g, _) in zip(rule_preds, SAMPLES)
                     if g <= 0.3 and p >= 0.3)

    print(f"\n规则法基线（affect_v3）:")
    print(f"  MAE {rule_mae:.4f}")
    print(f"  漏判 {rule_miss}/{n_pos}  误判 {rule_false}/{n_neg}")

    print(f"\n任务包 → {TASK}/packs.json（{len(packs)} 条）")
    print(f"  等待模型判断…")

    out = {"experiment": "E-152", "n": len(SAMPLES),
           "n_pos": n_pos, "n_neg": n_neg,
           "rule": {"mae": round(rule_mae, 4), "miss": rule_miss,
                    "false": rule_false}}
    with open(os.path.join(RESULTS, "e152_affect_model.json"), "w",
              encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)


if __name__ == "__main__":
    main()
