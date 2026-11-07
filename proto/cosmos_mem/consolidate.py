"""
consolidate.py —— 睡眠/巩固机制（Consolidation）

用户定义（docs/71）：
  "每一次模型自动觉得任务/项目已完结，感觉过了一个需要记忆点、
   需要整理回忆的节点之后，自动触发记忆整理、回忆和遗忘的睡眠机制。"
  "就像人一样，白天经历了一堆事，晚上睡觉自动整理、回忆强化、遗忘。"

职责边界（沿用 conflict.py 的原则）：
  【系统接管】确定性、机械的部分：
    · prepare_sleep_pack   扫描最近记忆 + 算两两相似 + 打包候选对
    · apply_sleep_verdict  按模型判定落盘（merge/invalidate/bump，只执行）
  【模型判断】语义部分：
    · 哪些记忆是"同一件事的碎片"→ merge
    · 哪些已经过时/被取代 → invalidate（遗忘）
    · 哪些是本阶段关键 → 强化（bump）

红线：
  · R2 不遗忘：遗忘 = invalidate 打 invalid_at，绝不物理删除
  · R4 可塑性：merge 保留旧记忆（superseded_by），失效可追溯
  · R1/R3：只操作 episodes 已有字段 + meta 表（channel 记账），不加表
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field, asdict

# 睡眠整理的候选对数量上限（避免一次打包太多）
MAX_SLEEP_PAIRS = 20
# 最近记忆扫描窗口
RECENT_WINDOW = 50


@dataclass
class SleepPair:
    """一对候选记忆（系统机械算出的"可能相关"对）"""
    a_id: str = ""
    a_text: str = ""
    b_id: str = ""
    b_text: str = ""
    cos: float = 0.0


@dataclass
class SleepPack:
    """睡眠整理候选包（发给模型）"""
    recent: list = field(default_factory=list)      # 最近记忆 [{ep_id, text}]
    pairs: list = field(default_factory=list)        # 相似对 [SleepPair]
    total: int = 0
    # 自上次睡眠以来的新增记忆（本次整理的主战场）
    fresh: list = field(default_factory=list)        # [{ep_id, text, level, age_h}]
    # L3 候选：需要模型判断"是否已过时"的记忆
    l3_candidates: list = field(default_factory=list)
    window: str = "recent"                           # "fresh" | "recent"
    cursor: float = 0.0                              # 上次睡眠时间戳


# ============================================================
# 睡眠游标：记录"上次睡到哪儿了"
# ============================================================

CURSOR_KEY = "sleep:cursor"


def get_cursor(st) -> float:
    """上次睡眠的时间戳（0 表示从未睡过）"""
    try:
        row = st.con.execute("SELECT val FROM meta WHERE key=?",
                             (CURSOR_KEY,)).fetchone()
        return float(row[0]) if row else 0.0
    except Exception:
        return 0.0


def set_cursor(st, ts: float | None = None) -> float:
    """更新睡眠游标（默认现在）"""
    import time as _t
    v = float(ts if ts is not None else _t.time())
    try:
        st.con.execute("INSERT OR REPLACE INTO meta VALUES (?,?)",
                       (CURSOR_KEY, str(v)))
        st.con.commit()
    except Exception:
        pass
    return v


# ============================================================
# 演化链：A → B → C 完整追溯（R4 可溯源的完整实现）
# ============================================================

def evolution_chain(st, ep_id: str, max_depth: int = 20) -> dict:
    """
    追溯一条记忆的完整演化链（纯机械查询，无判断）。

    双向追溯：
      · 向前：它取代了谁（superseded_by == ep_id）
      · 向后：它被谁取代（ep_id -> superseded_by -> ...）

    返回：
      {
        "root": 最老的一条,
        "tip":  最新的一条,
        "chain": [{ep_id, text, invalid_at, revision, is_current}, ...]  # 从老到新
      }
    """
    seen = set()
    chain = []

    def load(eid):
        if not eid or eid in seen:
            return None
        seen.add(eid)
        row = st.con.execute(
            "SELECT ep_id, raw_text, created_at, invalid_at, superseded_by, revision "
            "FROM episodes WHERE ep_id=?", (eid,)).fetchone()
        if not row:
            return None
        return {"ep_id": row[0], "text": row[1], "created_at": row[2],
                "invalid_at": row[3], "superseded_by": row[4],
                "revision": row[5], "is_current": row[3] is None}

    # ---- 1. 向源头回溯（谁取代了当前这条）----
    up = []
    cur = ep_id
    for _ in range(max_depth):
        row = st.con.execute(
            "SELECT superseded_by FROM episodes WHERE ep_id=?", (cur,)).fetchone()
        nxt = row[0] if row and row[0] else None
        if not nxt:
            break
        up.append(cur)
        cur = nxt
    tip_id = cur
    up.append(tip_id)

    # ---- 2. 从 tip 向历史追溯（tip 取代了谁）----
    cur = tip_id
    back = []
    for _ in range(max_depth):
        row = st.con.execute(
            "SELECT ep_id FROM episodes WHERE superseded_by=? "
            "ORDER BY created_at DESC LIMIT 1", (cur,)).fetchone()
        if not row:
            break
        cur = row[0]
        back.append(cur)
    back.reverse()

    # ---- 3. 组装从老到新的链 ----
    for eid in back + [tip_id]:
        item = load(eid)
        if item:
            chain.append(item)

    # ---- 4. 若当前这条自己就是末端，还要把它的前驱也带上 ----
    if not back and ep_id != tip_id:
        item = load(ep_id)
        if item:
            chain.insert(0, item)

    for it in chain:
        it.pop("superseded_by", None)

    return {
        "root": chain[0]["ep_id"] if chain else ep_id,
        "tip": tip_id,
        "length": len(chain),
        "chain": chain,
    }


def prepare_sleep_pack(st, embedder, limit: int = MAX_SLEEP_PAIRS,
                       recent_n: int = RECENT_WINDOW) -> SleepPack:
    """
    系统机械准备睡眠整理候选包。

    扫描窗口（2026-11-07 改进）：
      · 主窗口 = 自上次睡眠以来新增的记忆（增量，不漏老的）
      · 兜底   = 若增量不足 2 条，退回最近 recent_n 条（首次睡眠时用）

    L3 候选：
      · L0/L1/L2 受保护，不会被遗忘
      · L3 是"阶段性事实"，会随时间过时 —— 打包给模型判断是否该退役
      （判断权交模型，系统只负责挑出来）

    不做什么：不判关系、不判该不该合并/遗忘。
    """
    import numpy as np

    cursor = get_cursor(st)

    # ---------- 1. 增量窗口：上次睡眠后新增的 ----------
    # 首次睡眠（cursor=0）不叫"增量"，走兜底窗口更符合语义
    window = "recent"
    if cursor > 0:
        fresh_rows = st.con.execute(
            "SELECT ep_id, raw_text, created_at FROM episodes "
            "WHERE invalid_at IS NULL AND created_at > ? "
            "ORDER BY created_at ASC", (cursor,)).fetchall()
        if len(fresh_rows) >= 2:
            window = "fresh"
            rows = fresh_rows
        else:
            fresh_rows = []
    else:
        fresh_rows = []

    if window != "fresh":
        # 兜底：首次睡眠 / 增量太少，退回最近 recent_n 条
        rows = st.con.execute(
            "SELECT ep_id, raw_text, created_at FROM episodes "
            "WHERE invalid_at IS NULL ORDER BY created_at DESC LIMIT ?",
            (recent_n,)).fetchall()
        rows = list(reversed(rows))

    recent = [{"ep_id": r[0], "text": r[1]} for r in rows]

    # ---------- 2. L3 候选（可退役的阶段性事实）----------
    l3_candidates = []
    try:
        import hook as _hook
        import time as _t
        now = _t.time()
        for eid, txt, cat in rows:
            lv = _hook.level_of(st, eid)
            if lv is None:      # 未标记 = L3
                age_h = round((now - (cat or now)) / 3600.0, 1)
                l3_candidates.append({
                    "ep_id": eid, "text": txt, "age_h": age_h,
                })
    except Exception:
        pass

    if len(recent) < 2:
        return SleepPack(recent=recent, pairs=[], total=len(recent),
                         fresh=[], l3_candidates=l3_candidates,
                         window=window, cursor=cursor)

    # ---------- 3. 两两相似 ----------
    st._load_vectors(include_invalid=False)
    if st._vec is None or len(st._vec) < 2:
        return SleepPack(recent=recent, pairs=[], total=len(recent),
                         fresh=[], l3_candidates=l3_candidates,
                         window=window, cursor=cursor)

    idx_of = {eid: i for i, eid in enumerate(st._vec_ids)}
    recent_ids = [r["ep_id"] for r in recent if r["ep_id"] in idx_of]
    if len(recent_ids) < 2:
        return SleepPack(recent=recent, pairs=[], total=len(recent),
                         fresh=[], l3_candidates=l3_candidates,
                         window=window, cursor=cursor)

    norms = np.linalg.norm(st._vec, axis=1)
    norms[norms == 0] = 1.0
    idxs = [idx_of[e] for e in recent_ids]
    sub_vec = st._vec[idxs] / norms[idxs][:, None]

    sim_mat = sub_vec @ sub_vec.T
    text_of = {r["ep_id"]: r["text"] for r in recent}
    pairs = []
    for i in range(len(recent_ids)):
        for j in range(i + 1, len(recent_ids)):
            cos = float(sim_mat[i, j])
            pairs.append(SleepPair(
                a_id=recent_ids[i], a_text=text_of[recent_ids[i]],
                b_id=recent_ids[j], b_text=text_of[recent_ids[j]],
                cos=round(cos, 4),
            ))

    pairs.sort(key=lambda p: -p.cos)
    pairs = pairs[:limit]

    return SleepPack(
        recent=recent[:20],
        pairs=pairs,
        total=len(rows),
        fresh=[{"ep_id": r[0], "text": r[1]} for r in fresh_rows[:30]],
        l3_candidates=l3_candidates,
        window=window,
        cursor=cursor,
    )


def pack_to_dict(pack: SleepPack) -> dict:
    return {
        "recent": pack.recent,
        "pairs": [asdict(p) for p in pack.pairs],
        "total": pack.total,
        "fresh": pack.fresh,
        "l3_candidates": pack.l3_candidates,
        "window": pack.window,
        "cursor": pack.cursor,
    }


def _append_merged(st, keep_id: str, drop_id: str,
                   model_hint: str = "") -> bool:
    """
    程序化合并两条记忆：**原文一字不改**，只把 drop 的内容作为标注附在 keep 后。

    E-244：替代原来"用模型生成的 merged_text 覆盖 keep 原文"的做法。

    拼接格式（清晰可辨，可人工核对）：
        <keep 原文>
        ｜合并: <drop 原文>

    关于 model_hint：
      早期版本会把模型给的 merged_text 作为"备注"附上，
      但实测发现模型给的往往是一段**完整的重写文本**，
      附上去等于变相引入模型改写（E-244 测试发现的 bug）。
      因此现在**完全忽略 model_hint**——宁可少一条备注，
      也不让模型的重写内容混入记忆原文。

      若确实需要"更完整的表述"，应由模型显式调用 mem_note 写笔记，
      而不是偷偷改记忆。

    返回是否成功。
    """
    try:
        keep = st.get(keep_id)
        drop = st.get(drop_id)
        if not keep or not drop:
            return False
        k_text = (keep.raw_text or "").strip()
        d_text = (drop.raw_text or "").strip()
        if not d_text or d_text in k_text:
            # 已经包含，无需拼接
            return True

        # 只拼接原文，不掺入任何模型生成的文本
        merged = f"{k_text}\n\n｜合并: {d_text}"

        # 走 revise 以保留历史（revise 会先快照旧内容）
        return bool(st.revise(keep_id, new_text=merged))
    except Exception:
        return False


def apply_sleep_verdict(st, actions: list) -> dict:
    """
    按模型判定落盘（只执行，不判断）。

    actions 里的每项：
      { "op": "merge"|"invalidate"|"bump", ... }
        merge:       { op, keep_id, drop_id, merged_text }
                     把 drop_id 的内容并入 keep_id（revise keep_id），
                     drop_id 打 superseded_by=keep_id
        invalidate:  { op, ep_id }   ← 遗忘：打 invalid_at
        bump:        { op, ep_id, channel, n }  ← 强化：channel 记账 +n

    红线：
      · invalidate 打标记不删除
      · merge 保留旧记忆（superseded_by）
    """
    import channel as _ch
    import hook as _hook

    result = {"merged": [], "invalidated": [], "bumped": [],
              "skipped_hooks": []}

    for a in actions or []:
        op = a.get("op")
        try:
            if op == "merge":
                keep_id = a.get("keep_id")
                drop_id = a.get("drop_id")
                merged_text = a.get("merged_text") or ""
                # 分层保护（docs/72）：L0/L1/L2 不可被合并掉，也不可被覆盖
                if keep_id and not _hook.can_revise(st, keep_id):
                    result["skipped_hooks"].append(keep_id)
                    continue
                if drop_id and not _hook.can_merge(st, drop_id):
                    result["skipped_hooks"].append(drop_id)
                    continue
                if keep_id and drop_id:
                    # E-244 修：不再让模型提供 merged_text 覆盖原文。
                    #
                    # 原实现用模型生成的 merged_text 调 st.revise 覆盖 keep 的原文，
                    # 这有两处违反原则：
                    #   ① 与 E-243 冲突——模型不该重写内容，只该做结构编排
                    #   ② 原文实际被改写，虽然现在有 history 兜底，
                    #      但"改写记忆原文"本身不该是睡眠的默认行为
                    #
                    # 新做法（程序拼接，原文一字不改）：
                    #   keep 的正文保持原样，drop 的内容作为附录标注附在 keep 后面。
                    #   若要真正合成更完整的表述，由模型显式调用 mem_note 去写笔记，
                    #   而不是偷偷改记忆。
                    merged = _append_merged(st, keep_id, drop_id, merged_text)
                    result["merged"].append(
                        {"keep": keep_id, "drop": drop_id,
                         "mode": "program_append" if merged else "skip"})
                    # drop 打 superseded_by（不删）
                    if merged:
                        st.invalidate(drop_id, superseded_by=keep_id)
            elif op == "invalidate":
                ep_id = a.get("ep_id")
                if ep_id and not _hook.can_invalidate(st, ep_id):
                    result["skipped_hooks"].append(ep_id)
                    continue
                if ep_id and st.get(ep_id):
                    if st.invalidate(ep_id):
                        result["invalidated"].append(ep_id)
            elif op == "bump":
                ep_id = a.get("ep_id")
                ch = a.get("channel") or "recall_by_query"
                n = int(a.get("n") or 1)
                if ep_id and st.get(ep_id):
                    _ch.bump(st, ep_id, ch, n)
                    result["bumped"].append(ep_id)
        except Exception:
            continue

    return result


def log_sleep(st, actions: list, result: dict) -> None:
    """记录睡眠日志到 meta 表（供 UI 展示）"""
    try:
        key = "sleep:log"
        row = st.con.execute("SELECT val FROM meta WHERE key=?", (key,)).fetchone()
        log = json.loads(row[0]) if row else []
        log.insert(0, {
            "at": time.time(),
            "actions": len(actions or []),
            "merged": result.get("merged", []),
            "invalidated": result.get("invalidated", []),
            "bumped": result.get("bumped", []),
        })
        log = log[:50]
        st.con.execute("INSERT OR REPLACE INTO meta VALUES (?,?)",
                       (key, json.dumps(log, ensure_ascii=False)))
        st.con.commit()
    except Exception:
        pass
