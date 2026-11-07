"""
channel.py —— 多通道熟练度（"手上有惯性"）

用户定义（docs/49）：
  "门锁密码我根本都不会记……刚开始记一次，有一次忘了，去查一下，
   查了这两三次之后就记住了。不是我脑子在每次回忆，而是我手上有惯性了，
   手就直接随着这种惯性，不需要动脑子就能按出来。"

  "像妈妈的名字……这种东西都已经成了惯性了，都不需要我去动脑。
   比如说，我天天喊我妈妈的名字，但是我很少写。有时候我写她的名字
   还要想一下怎么写，因为她的名字有生僻字。"

关键洞察（我此前理解错的）：
  · 固定席位 ≠ "一小批重要记忆清单"
  · 固定席位 = **某些通道被反复走通形成的惯性**
  · 同一件事可以有多个通道，各自独立熟练（会说 ≠ 会写）

设计：
  · 不是给记忆打一个"重要性分"，而是记录"通过哪种方式被访问过多少次"
  · 各通道计数独立
  · 全部存在 meta 表（不加新表 → schema 不变 → R1/R3 风险最低）

通道定义：
  recall_by_query   被查询直接命中（"去查一下"）
  recall_by_fixed   作为固定席位直接出现（"不用想就出来"）
  recall_by_chain   通过链式联想到达（"顺藤摸瓜"）
  recall_by_merge   被融合时用到（"整理想起来"）
  recall_by_prompt  被注入到对话上下文（"见着就想起来"）
"""
from __future__ import annotations

import json

# 通道白名单（防止拼写错误产生垃圾键）
CHANNELS = (
    "recall_by_query",
    "recall_by_fixed",
    "recall_by_chain",
    "recall_by_merge",
    "recall_by_prompt",
)


def _key(ep_id: str) -> str:
    return f"channel:{ep_id}"


def get_channels(st, ep_id: str) -> dict:
    """
    读取某条记忆的全部通道计数。

    返回格式：{"recall_by_query": 12, "recall_by_fixed": 3, ...}
    缺失的通道补 0，保证结构稳定（便于迁移比对）。
    """
    out = {c: 0 for c in CHANNELS}
    row = st.con.execute("SELECT val FROM meta WHERE key=?",
                         (_key(ep_id),)).fetchone()
    if not row:
        return out
    try:
        d = json.loads(row[0])
        if isinstance(d, dict):
            for c in CHANNELS:
                try:
                    out[c] = int(d.get(c) or 0)
                except Exception:
                    out[c] = 0
    except Exception:
        pass
    return out


def bump(st, ep_id: str, channel: str, n: int = 1) -> dict:
    """
    某条记忆在指定通道上 +n。

    这是"走通一次路径"的记账，不是打分。
    只增不减（R2 不遗忘：熟练度不会因为时间而回退）。
    """
    if channel not in CHANNELS:
        return get_channels(st, ep_id)
    cur = get_channels(st, ep_id)
    cur[channel] = cur.get(channel, 0) + max(0, int(n))
    try:
        st.con.execute(
            "INSERT OR REPLACE INTO meta VALUES (?,?)",
            (_key(ep_id), json.dumps(cur, ensure_ascii=False)))
        st.con.commit()
    except Exception:
        pass
    return cur


def bump_many(st, ep_ids, channel: str, n: int = 1) -> int:
    """批量记账（减少 commit 次数）"""
    if channel not in CHANNELS:
        return 0
    cnt = 0
    for eid in ep_ids:
        cur = get_channels(st, eid)
        cur[channel] = cur.get(channel, 0) + max(0, int(n))
        try:
            st.con.execute(
                "INSERT OR REPLACE INTO meta VALUES (?,?)",
                (_key(eid), json.dumps(cur, ensure_ascii=False)))
            cnt += 1
        except Exception:
            pass
    try:
        st.con.commit()
    except Exception:
        pass
    return cnt


def total_visits(ch: dict) -> int:
    """所有通道的访问总数"""
    return sum(int(ch.get(c) or 0) for c in CHANNELS)


def dominant_channel(ch: dict) -> str:
    """哪条通道最熟练（对应"哪种方式最顺"）"""
    best, best_n = "", 0
    for c in CHANNELS:
        n = int(ch.get(c) or 0)
        if n > best_n:
            best, best_n = c, n
    return best


def familiarity(ch: dict) -> float:
    """
    熟练度（0~1 的归一化指标，仅用于诊断和可选的排序加成）。

    设计：用对数压缩，避免高频记忆完全垄断。
      familiarity = log(1 + total) / log(1 + total + K)
    这里取 K=10：访问 10 次达到约 0.5，100 次约 0.67。

    注意：这是**诊断指标**，不是"重要性分数"。
    是否用于排序，必须经过 A/B 实验决定（不预设）。
    """
    import math
    t = total_visits(ch)
    if t <= 0:
        return 0.0
    return round(math.log(1 + t) / math.log(1 + t + 10.0), 4)


def stats(st, limit: int = 10) -> dict:
    """全库通道统计（诊断用）"""
    rows = st.con.execute(
        "SELECT key, val FROM meta WHERE key LIKE 'channel:%'").fetchall()
    n_mem = 0
    tot = {c: 0 for c in CHANNELS}
    most = []
    for k, v in rows:
        try:
            d = json.loads(v)
        except Exception:
            continue
        n_mem += 1
        ch = {c: int(d.get(c) or 0) for c in CHANNELS}
        for c in CHANNELS:
            tot[c] += ch[c]
        t = total_visits(ch)
        if t > 0:
            most.append((t, k.replace("channel:", ""), ch))
    most.sort(key=lambda x: -x[0])
    return {
        "memories_tracked": n_mem,
        "by_channel": tot,
        "total_visits": sum(tot.values()),
        "top": [{"ep_id": e, "visits": t, "channels": ch}
                for t, e, ch in most[:limit]],
    }
