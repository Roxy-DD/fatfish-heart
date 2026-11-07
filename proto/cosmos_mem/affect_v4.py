"""
affect_v4.py —— 情绪强度：模型判断（写入时算，检索时零成本）

E-152 实测结论（50 条标注样本）：
  规则法 affect_v3 : MAE 0.2702  漏判 17/25  相关 0.465
  模型判断         : MAE 0.0598  漏判 1/25   相关 0.967
  → MAE 降低 78%，漏判从 68% 降到 4%

E-151 的反面证据：
  规则预筛（叙事特征词）+ 模型精筛，最多覆盖 65% 真情绪
  因为"预筛"本身就是一次情绪判断，规则做不了

用户原则（docs/49）：
  "情绪不是思考出来的，就像小脑自动处理了……是生理反应"
  → 情绪识别必须是"自动"的
  → 实现方式：**写入时一次算好，检索时直接读**（不是不调模型，而是不阻塞用户）

设计：
  · 主路径：模型判断（插件层通过 judge_fn 注入）
  · 降级路径：无模型时退回 affect_v3（规则），保证系统不崩
  · 所有值存 episodes.affect_peak / valence（原有字段，不动 schema → R1 安全）
"""
from __future__ import annotations

import json
import re


# ============================================================
# 一、系统接管：给模型的 prompt 构造 + 输出的机械解析
# ============================================================
AFFECT_SYSTEM_PROMPT = """你在判断一条记忆的**情绪强度**。这是纯判断任务，只输出 JSON。

对给定文本，判断"这句话所描述的事件本身，带来多强的情绪冲击"，给出：

1. affect：0.0 ~ 1.0
   0.0 = 完全没有情绪，纯技术/规则/流程/日常事实陈述
   0.3 = 有轻微情绪底色
   0.6 = 明显情绪
   1.0 = 极强情绪（丧亲、重大创伤、极度喜悦）

2. valence：-1 / 0 / +1
   -1 = 负面（悲伤/痛苦/恐惧/愤怒/羞耻/焦虑）
   0  = 中性或无明显方向
   +1 = 正面（喜悦/兴奋/欣慰/感动）
   若 affect = 0，valence 必须为 0

关键判据：
- 情绪不一定靠情绪词表达。
  「爷爷去世了，那阵子我一直在医院和家之间来回跑」表面平静但冲击很强（≈0.9）
  「上个月忘带钥匙被锁在门外，在楼道坐了两个小时」无情绪词但有挫败感（≈0.6）
  「小时候夏天傍晚在院子里吃饭，天还很亮」温馨回忆（≈0.45）
- 但技术、规则、流程、日常事实是 0：
  「我电脑的登录密码是 8848」→ 0
  「客户要求所有接口响应时间低于200ms」→ 0
  「我每天早上七点起床」→ 0
- 区分"陈述事实"和"讲述经历"：
  「张伟说他改完了接口」→ 工作事实，0
  「同事在所有人面前说我做的东西是垃圾」→ 经历+羞辱，0.88

只输出：{"affect":0.00,"valence":0,"reason":"一句话"}"""


def build_prompt(text: str) -> tuple[str, str]:
    """系统构造 prompt（不做判断）"""
    return AFFECT_SYSTEM_PROMPT, f"文本：{text}"


def parse_affect_output(raw: str) -> dict | None:
    """
    机械解析模型输出。

    返回 {"affect": float, "valence": int, "reason": str} 或 None
    """
    if not raw:
        return None
    m = re.search(r"\{[\s\S]*\}", raw)
    if not m:
        return None
    try:
        d = json.loads(m[0])
    except Exception:
        return None
    if not isinstance(d, dict):
        return None

    try:
        affect = float(d.get("affect"))
    except Exception:
        return None
    # 系统校验：钳制到 [0,1]
    affect = max(0.0, min(1.0, affect))
    affect = round(affect, 3)

    try:
        valence = int(d.get("valence") or 0)
    except Exception:
        valence = 0
    if valence not in (-1, 0, 1):
        valence = 0
    # 一致性：affect=0 时 valence 必须 0
    if affect <= 0.0:
        valence = 0

    return {
        "affect": affect,
        "valence": valence,
        "reason": str(d.get("reason") or "")[:120],
    }


# ============================================================
# 二、降级路径：无模型时用规则（affect_v3）
# ============================================================
def fallback_rule(text: str, explicit: bool = False) -> dict:
    """
    规则降级（affect_v3）。

    E-152 实测：MAE 0.2702，漏判 68%。
    仅在拿不到模型判断时使用，保证系统不崩。
    """
    try:
        from affect_v3 import estimate_affect_v3
        r = estimate_affect_v3(text, explicit=explicit)
        return {
            "affect": round(float(r.get("affect_word") or 0), 3),
            "valence": int(round(float(r.get("valence") or 0))),
            "reason": "规则降级（affect_v3）",
            "source": "rule",
        }
    except Exception:
        return {"affect": 0.0, "valence": 0, "reason": "规则失败",
                "source": "none"}


# ============================================================
# 三、统一入口：写入时调用
# ============================================================
def estimate_affect_v4(text: str, judge_fn=None, explicit: bool = False) -> dict:
    """
    估计情绪强度。

    参数：
      judge_fn  模型判断回调 fn(system, user) -> raw_output(str)
                由插件层注入（内部调 LLM）。
                为 None 时走规则降级。

    返回：
      {"affect": 0.0~1.0, "valence": -1/0/1, "reason": str, "source": "model"/"rule"}
    """
    if judge_fn is not None:
        try:
            system, user = build_prompt(text)
            raw = judge_fn(system, user)
            parsed = parse_affect_output(raw)
            if parsed:
                parsed["source"] = "model"
                return parsed
        except Exception:
            pass

    return fallback_rule(text, explicit=explicit)


# ============================================================
# 四、系统机械部分：效价方向的一致性校验
# ============================================================
def resonance_score(a_affect: float, a_valence: float,
                    b_affect: float, b_valence: float) -> float:
    """
    情绪共振强度（纯数学，无判断成分）。

    对应 store_v1._expand 的路 C，但不再用硬阈值判断"是否共振"，
    而是给出一个 0~1 的连续分数，交由上层决定怎么用。

    E-149 实测：原实现要求 affect>=0.15 且 valence!=0，
    而真实库只有 3.1% 的记忆满足 |valence|>=0.15 → 这条路几乎不触发。

    共振的两个条件：
      · 效价同向（都是负面或都是正面）
      · 强度接近（不能一个是 0.9 一个是 0.1）
    """
    a = max(0.0, min(1.0, float(a_affect or 0)))
    b = max(0.0, min(1.0, float(b_affect or 0)))
    av = float(a_valence or 0)
    bv = float(b_valence or 0)

    # 任一为 0 → 无共振
    if a <= 0.05 or b <= 0.05:
        return 0.0
    # 效价必须同向且非零
    if av == 0 or bv == 0 or av * bv <= 0:
        return 0.0

    # 强度相似度：1 - |a-b|
    strength_sim = 1.0 - abs(a - b)
    # 强度本身也要够（取两者较小值作为权重）
    magnitude = min(a, b)
    return round(strength_sim * magnitude, 4)
