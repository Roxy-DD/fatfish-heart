"""
E-108 两阶段记忆循环：模型判断质量的受控测试

用户要求：自己设计测试方案。

测试目标（两件事）：
  A. 阶段2的核心能力——模型能否正确判断"哪些该记、哪些不该记"
  B. 与旧版关键词法对比——模型判断是否真的更好

设计原则（避免自欺）：
  · 用固定测试集，有 gold 标注，可重复
  · 同时测精确率和召回率（只看一个会骗自己）
  · 对照组：旧版关键词法在同一批数据上的表现
  · 用插件里一模一样的 prompt，不是另写一套

输出：测试包 → 交给模型判断 → 算 P/R/F1 → 对比关键词法
"""
from __future__ import annotations

import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, "cosmos_mem"))
RESULTS = os.path.join(_HERE, "results")
TASK = os.path.join(_HERE, "work", "e108")
os.makedirs(TASK, exist_ok=True)

# 测试集：12 条用户输入，人工标注"该不该记"
# gold=True 表示值得长期记住
CASES = [
    # ---- 该记（长期有效的事实/偏好/承诺/关系）----
    ("我下周要去北京出差三天", True, "行程安排，未来会问起"),
    ("我女儿今年上三年级了", True, "长期有效的事实"),
    ("以后叫我老张就行", True, "称呼偏好，长期有效"),
    ("我每天早上七点起床", True, "生活习惯"),
    ("我的车是白色的卡罗拉，车牌沪A·5X29K", True, "长期有效的事实"),
    ("记住：这个项目的验收标准是延迟低于5毫秒", True, "明确要求记住的约定"),
    ("我老婆对花生过敏，家里不能有花生", True, "关系+安全，长期有效"),
    ("我答应这周五之前把接口文档发给王工", True, "承诺，未来要兑现"),

    # ---- 不该记（一次性/操作/寒暄/问句）----
    ("帮我查一下今天北京的天气", False, "一次性操作"),
    ("你好", False, "寒暄"),
    ("这个函数的时间复杂度是多少？", False, "问句"),
    ("好的，继续", False, "回应指令"),
    ("刚才那个输出有点长，能不能短一点", False, "临时反馈"),
    ("跑一下测试看看", False, "正在进行的操作"),
    ("嗯，我看看", False, "临时状态"),
    ("把这段代码格式化一下", False, "一次性操作"),
]

# 插件里的 prompt（一模一样，保证测的是同一件事）
PLUGIN_SYSTEM_PROMPT = (
    '你是记忆系统的"复盘分析器"。判断用户说过的内容里，哪些值得长期记住。\n\n'
    '该记：\n'
    '1. 长期有效的事实（密码、编号、地址、日期、参数、约定、承诺）\n'
    '2. 用户的偏好、习惯、关系、身份信息\n'
    '3. 影响未来决策的结论或决定\n'
    '不该记：\n'
    '1. 一次性的寒暄、问句、临时状态\n'
    '2. 正在进行的操作（"帮我查一下""跑个测试"）\n'
    '3. 已经记过的重复内容\n\n'
    '重要：宁可少记，不要记垃圾。每条记忆要写成完整、独立可读的句子。\n'
    '只输出 JSON，不要解释。\n'
    '格式：{"memories":[{"text":"完整记忆","reason":"为什么值得记","importance":0.0到1.0}]}\n'
    '若无值得记的，输出 {"memories":[]}'
)

# 旧版关键词法（对照组）
REMEMBER_SIGNALS = [
    '记住', '别忘了', '别忘记', '记一下', '帮我记', '要记得', '记牢',
    '密码是', '密码为', '我的密码', '账号是', '卡号是', '电话是',
    '地址是', '生日是', '截止', '到期', '必须', '一定要', '保存',
]


def keyword_judge(text: str) -> bool:
    """旧版：关键词匹配"""
    if not text or len(text) < 6 or len(text) > 400:
        return False
    if text.rstrip().endswith(('?', '？')):
        return False
    return any(s in text for s in REMEMBER_SIGNALS)


def main():
    rows = []
    for i, (text, gold, why) in enumerate(CASES):
        rows.append({"cid": f"C{i:02d}", "text": text,
                     "gold": gold, "note": why})

    # 生成给模型的任务包
    pack = {
        "system": PLUGIN_SYSTEM_PROMPT,
        "inputs": [{"cid": r["cid"], "text": r["text"]} for r in rows],
    }
    with open(os.path.join(TASK, "pack.json"), "w", encoding="utf-8") as f:
        json.dump(pack, f, ensure_ascii=False, indent=1)

    # 答案键（不给模型）
    with open(os.path.join(TASK, "gold.json"), "w", encoding="utf-8") as f:
        json.dump({r["cid"]: {"gold": r["gold"], "text": r["text"],
                              "note": r["note"]} for r in rows},
                  f, ensure_ascii=False, indent=1)

    # 关键词法基线（本地直接算）
    kw_ok = 0
    print("=" * 88)
    print("E-108 测试集与关键词法基线")
    print("=" * 88)
    print(f"\n{len(rows)} 条（8 该记 / 8 不该记）\n")
    print("-" * 88)
    print(f"{'cid':<6}{'gold':<8}{'关键词判':<10}{'对?':<6}{'内容'}")
    print("-" * 88)
    for r in rows:
        kw = keyword_judge(r["text"])
        ok = (kw == r["gold"])
        kw_ok += 1 if ok else 0
        print(f"{r['cid']:<6}{str(r['gold']):<8}{str(kw):<10}"
              f"{'✓' if ok else '✗':<6}{r['text'][:34]}")
    print("-" * 88)
    print(f"\n关键词法准确率: {kw_ok}/{len(rows)} = {kw_ok/len(rows):.4f}")

    with open(os.path.join(RESULTS, "e108_baseline.json"), "w", encoding="utf-8") as f:
        json.dump({"experiment": "E-108-baseline", "n": len(rows),
                   "keyword_accuracy": round(kw_ok / len(rows), 4)},
                  f, ensure_ascii=False, indent=1)

    print(f"\n→ 任务包: {TASK}/pack.json")
    print("→ 下一步：交给模型判断，再与 gold 对比")


if __name__ == "__main__":
    main()
