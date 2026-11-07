"""
triage.py —— 记忆分诊：用现有数据决定"该记在哪"

用户要求（2026-11-07）：
  "我们不可能去背诵书，也不可能记住所有的自然规律，
   但是我们会记住一些重要或者常用的东西。
   而那些不常用的、少见但重要的，或者少见又不重要的东西，
   我们经常会把它体系性地记录在笔记本里。"
  "少见，但是重要，可能也得记在你的记忆里。但是呢，这种决策，
   我觉得你可以自己去迭代，或者自己去验证或者你去直接搜，
   或者根据这种相关性自主迭代了。"

判断依据（**不发明新指标，只用系统已有的两维数据**）：

  常用度 frequency  ← channel.py 的访问计数（recall_by_query + recall_by_fixed）
  重要性 importance ← cost（失去代价，用户定义的核心维度）

四象限处置：

  ┌─────────────┬──────────────────┬──────────────────┐
  │             │  重要 (cost 高)   │  不重要 (cost 低) │
  ├─────────────┼──────────────────┼──────────────────┤
  │ 常用(访问多) │ 留在记忆+固定席位  │ 留在记忆          │
  │ 少见(访问少) │ 留记忆 + 写详细笔记│ 写简短随笔        │
  └─────────────┴──────────────────┴──────────────────┘

关键：**少见但重要的，两处都记**（记忆留一份保证能检索到，
      笔记写详细一份保证不靠记忆硬撑）。

这是纯统计判定，无判断成分；"要不要真的动手写笔记"
仍由模型决定（通过生成 note_hint 待办）。
"""
from __future__ import annotations

import json

# 阈值（可在实验后调整，当前是基于现有数据分布的经验值）
FREQ_HIGH = 3       # 访问次数 >= 3 算"常用"
COST_HIGH = 0.7     # cost >= 0.7 算"重要"

# 处置策略
STRATEGIES = {
    "core":     "留在记忆（固定席位）",           # 常用 + 重要
    "common":   "留在记忆",                       # 常用 + 不重要
    "critical": "留记忆 + 写详细笔记",            # 少见 + 重要
    "minor":    "写简短随笔",                     # 少见 + 不重要
}


def _visits(st, ep_id: str) -> int:
    """该记忆的总访问次数（所有通道求和）"""
    try:
        import channel as _ch
        ch = _ch.get_channels(st, ep_id)
        return _ch.total_visits(ch)
    except Exception:
        return 0


def _cost(st, ep_id: str) -> float:
    try:
        row = st.con.execute(
            "SELECT cost FROM episodes WHERE ep_id=?", (ep_id,)).fetchone()
        return float(row[0] or 0.0) if row else 0.0
    except Exception:
        return 0.0


def classify(st, ep_id: str,
             freq_high: int = FREQ_HIGH,
             cost_high: float = COST_HIGH) -> dict:
    """
    给单条记忆分诊。

    返回：
      {
        "ep_id": ...,
        "visits": 5, "cost": 0.9,
        "freq": "high"|"low", "imp": "high"|"low",
        "quadrant": "core"|"common"|"critical"|"minor",
        "strategy": "留在记忆（固定席位）",
        "text": ...
      }
    """
    visits = _visits(st, ep_id)
    cost = _cost(st, ep_id)
    freq = "high" if visits >= freq_high else "low"
    imp = "high" if cost >= cost_high else "low"

    if freq == "high" and imp == "high":
        q = "core"
    elif freq == "high" and imp == "low":
        q = "common"
    elif freq == "low" and imp == "high":
        q = "critical"
    else:
        q = "minor"

    row = st.con.execute(
        "SELECT raw_text FROM episodes WHERE ep_id=?", (ep_id,)).fetchone()
    if not row:
        # E-251：记忆不存在时明确标记，不要伪造一个 "minor" 结论
        return {
            "ep_id": ep_id,
            "text": "",
            "exists": False,
            "visits": 0, "cost": 0.0,
            "freq": "unknown", "imp": "unknown",
            "quadrant": "not_found",
            "strategy": "记忆不存在，无法分诊",
        }
    return {
        "ep_id": ep_id,
        "text": row[0],
        "exists": True,
        "visits": visits, "cost": round(cost, 3),
        "freq": freq, "imp": imp,
        "quadrant": q,
        "strategy": STRATEGIES[q],
    }


def triage_all(st, freq_high: int = FREQ_HIGH,
               cost_high: float = COST_HIGH,
               limit: int = 500) -> dict:
    """
    对全部有效记忆做分诊，返回四象限分布。

    用途：
      · 看整体结构（有多少该写笔记的没写）
      · 找出"少见但重要"的（这些最容易被忽略，但其实最该写笔记）
    """
    rows = st.con.execute(
        "SELECT ep_id FROM episodes WHERE invalid_at IS NULL "
        "ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()

    buckets = {"core": [], "common": [], "critical": [], "minor": []}
    for (eid,) in rows:
        c = classify(st, eid, freq_high, cost_high)
        buckets[c["quadrant"]].append(c)

    return {
        "total": len(rows),
        "counts": {k: len(v) for k, v in buckets.items()},
        "core": buckets["core"],
        "common": buckets["common"],
        "critical": buckets["critical"],
        "minor": buckets["minor"],
        "thresholds": {"freq_high": freq_high, "cost_high": cost_high},
    }


def suggest_from_triage(st, note_hint_min: int = 3,
                        freq_high: int = FREQ_HIGH,
                        cost_high: float = COST_HIGH) -> list[dict]:
    """
    根据分诊结果，生成"该写笔记"的建议。

    规则（纯机械）：
      · critical（少见但重要）→ 优先建议写详细笔记
      · minor（少见又不重要）→ 建议写简短随笔
      同类攒够 note_hint_min 条才建议（避免太碎）

    返回建议列表，供模型决定要不要真的写。
    """
    t = triage_all(st, freq_high, cost_high)

    out = []
    for quad, tier, urgency in (
        ("critical", "note", "high"),
        ("minor", "scrap", "low"),
    ):
        items = t[quad]
        if len(items) < note_hint_min:
            continue
        out.append({
            "quadrant": quad,
            "suggest_tier": tier,
            "urgency": urgency,
            "count": len(items),
            "ep_ids": [x["ep_id"] for x in items[:20]],
            "samples": [x["text"][:60] for x in items[:3]],
            "reason": STRATEGIES[quad],
        })
    return out


def stats(st) -> dict:
    t = triage_all(st, limit=10000)
    return {
        "total": t["total"],
        "counts": t["counts"],
        "thresholds": t["thresholds"],
        "needs_note": t["counts"]["critical"],
        "needs_scrap": t["counts"]["minor"],
    }


__all__ = ["classify", "triage_all", "suggest_from_triage", "stats",
           "STRATEGIES", "FREQ_HIGH", "COST_HIGH"]
