"""
E-151 修正 C 方案的触发条件

E-150 的问题：
  触发条件写成 (rule < 0.3) and narrative
  → 只覆盖 9/20 条真情绪（召回 45%）
  → 漏掉的都是"规则给了 0.3~0.6 但真实是 0.8~0.9"的样本

根因：
  规则给出的非零值不代表"判对了"，只代表"命中了一个词"。
  真正该触发的是：**有叙事特征 + 规则值与"这条文本可能的情绪上限"差距大**

更本质的判断：
  规则法能命中的是"显式情绪词"。
  如果文本有叙事特征（人物+动作+场景）但规则值不高，就是规则法的盲区。

修正触发条件候选：
  T1  rule < 0.3  and narrative      （E-150 原方案，召回 45%）
  T2  rule < 0.6  and narrative      （放宽阈值）
  T3  narrative only                  （有叙事就问，最激进）
  T4  rule < 0.7  and narrative and len(text) >= 12

评估每个条件的：
  · 触发比例（成本）
  · 真情绪覆盖召回
  · 误触发数（问了但没情绪，浪费）
"""
from __future__ import annotations

import json
import os
import re
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, "cosmos_mem"))
RESULTS = os.path.join(_HERE, "results")

SAMPLES = [
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
    # 补充：技术描述但含"人物+动作"的（测试误触发）
    ("我昨天部署了Redis 7", 0.0),
    ("张伟说他改完了接口", 0.0),
    ("李娜在会议室演示了原型", 0.0),
]

PERSON = re.compile(
    r"(我|你|他|她|我们|他们|妈妈|爸爸|爷爷|奶奶|外婆|外公|孩子|"
    r"同事|老师|同学|朋友|初恋|张伟|李娜|王强|赵敏)")
ACTION = re.compile(
    r"(去世|走|住院|手术|哭|抖|吵架|分手|辞退|拆|搬|第一次|"
    r"忘|丢|锁|摔|骂|念|站|坐|睡|吃|玩|看|喊|叫|发怵|梦见|眼泪)")
SCENE = re.compile(
    r"(小时候|那年|那天|那时候|以前|曾经|半夜|凌晨|冬天|夏天|"
    r"院子|江边|楼道|医院|老家|台上|全班|所有人|整宿|半个月)")


def narrative_level(text):
    """叙事强度：0=无，1=有人物，2=人物+动作，3=人物+动作+场景"""
    t = text or ""
    p = bool(PERSON.search(t))
    a = bool(ACTION.search(t))
    s = bool(SCENE.search(t))
    if p and a and s:
        return 3
    if p and a:
        return 2
    if p:
        return 1
    return 0


def rule_affect(text):
    from affect_v3 import estimate_affect_v3
    return float(estimate_affect_v3(text, explicit=False).get("affect_word") or 0)


def main():
    print("=" * 96)
    print("E-151 修正 C 方案触发条件")
    print("=" * 96)

    rows = []
    for text, gold in SAMPLES:
        rows.append({
            "text": text, "gold": gold,
            "rule": rule_affect(text),
            "narr": narrative_level(text),
            "len": len(text),
        })

    n_pos = sum(1 for r in rows if r["gold"] > 0.3)
    n_neg = len(rows) - n_pos
    print(f"\n{len(rows)} 条（有情绪 {n_pos} / 无情绪 {n_neg}）")

    # ---------- 触发条件对比 ----------
    TRIGGERS = {
        "T1 rule<0.3 & narr>=2": lambda r: r["rule"] < 0.3 and r["narr"] >= 2,
        "T2 rule<0.6 & narr>=2": lambda r: r["rule"] < 0.6 and r["narr"] >= 2,
        "T3 narr>=3": lambda r: r["narr"] >= 3,
        "T4 narr>=2": lambda r: r["narr"] >= 2,
        "T5 rule<0.6 & narr>=3": lambda r: r["rule"] < 0.6 and r["narr"] >= 3,
        "T6 rule<0.65 & narr>=2 & len>=12": lambda r: (
            r["rule"] < 0.65 and r["narr"] >= 2 and r["len"] >= 12),
    }

    print("\n" + "-" * 96)
    print("触发条件对比")
    print("-" * 96)
    print(f"{'条件':<34}{'触发率':<10}{'覆盖率':<10}{'误触发':<10}{'成本/条'}")
    print("-" * 96)

    best = None
    for name, fn in TRIGGERS.items():
        trig = [r for r in rows if fn(r)]
        cover = sum(1 for r in trig if r["gold"] > 0.3)
        false_trig = sum(1 for r in trig if r["gold"] <= 0.3)
        ratio = len(trig) / len(rows) * 100

        # 成本估算：触发率 × 200ms
        cost = ratio / 100 * 200

        print(f"{name:<34}{f'{ratio:.0f}%':<10}"
              f"{f'{cover}/{n_pos}':<10}{false_trig:<10}{cost:.0f}ms")

        score = cover / n_pos
        if best is None or (score > best["coverage"] and false_trig <= 3):
            best = {"name": name, "coverage": score,
                    "false": false_trig, "ratio": ratio,
                    "cover": cover, "cost": cost}

    # ---------- 最优方案的细节 ----------
    if best:
        print("\n" + "=" * 96)
        print(f"最优方案：{best['name']}")
        print("=" * 96)
        fn = TRIGGERS[best["name"]]
        trig = [r for r in rows if fn(r)]
        miss = [r for r in rows if r["gold"] > 0.3 and not fn(r)]
        print(f"\n  触发 {len(trig)} 条，覆盖 {best['cover']}/{n_pos} 条真情绪"
              f"（{best['coverage']:.0%}）")
        print(f"  误触发 {best['false']} 条")
        print(f"  平均成本 {best['cost']:.0f}ms/条")

        if miss:
            print(f"\n  仍未覆盖的真情绪（{len(miss)} 条）:")
            for r in miss:
                print(f"    gold={r['gold']:.2f} rule={r['rule']:.2f} "
                      f"narr={r['narr']}  {r['text'][:44]}")

        print(f"\n  误触发的（问了但没情绪）:")
        for r in trig:
            if r["gold"] <= 0.3:
                print(f"    rule={r['rule']:.2f} narr={r['narr']}  "
                      f"{r['text'][:44]}")

    # ---------- 结论 ----------
    print("\n" + "=" * 96)
    print("结论")
    print("=" * 96)

    from affect_v3 import estimate_affect_v3
    a_mae = sum(
        abs(float(estimate_affect_v3(t, explicit=False).get("affect_word") or 0) - g)
        for t, g in SAMPLES) / len(SAMPLES)
    print(f"\n  A 纯规则 MAE: {a_mae:.4f}")
    if best:
        print(f"  C 两级（{best['name']}）:")
        print(f"    触发率 {best['ratio']:.0f}%，覆盖 {best['coverage']:.0%} 真情绪")
        print(f"    平均成本 {best['cost']:.0f}ms/条")
        print(f"\n  → 相比纯规则的 MAE {a_mae:.3f}，C 方案能覆盖 "
              f"{best['coverage']:.0%} 的规则盲区")

    out = {"experiment": "E-151",
           "n": len(rows), "n_pos": n_pos,
           "rule_mae": round(a_mae, 4),
           "best": best}
    with open(os.path.join(RESULTS, "e151_trigger.json"), "w",
              encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(f"\n写入 {os.path.join(RESULTS, 'e151_trigger.json')}")


if __name__ == "__main__":
    main()
