"""
merge.py —— 重复记忆融合（两段式后半段）

用户设计（2026-11-02）：
  "完全重复的当然要直接合并。有差异的情况下，就要让模型自己判断，
   让它去分析里面到底有哪些差异，然后把它合并保留成一条更完整的。"

职责划分：
  系统：归一化比对判"完全重复"、选主记录、落盘、记审计
  模型：判"是不是同一件事"、分析差异、融合成更完整的一条

红线约束（R2 不遗忘）：
  · 绝不物理删除任何一条
  · 被合并的记录标 superseded_by → 主记录 id
  · 融合前的原文全部保留在 meta 审计记录里
  · 融合后的文本写入主记录（revision+1），原文也保留在审计里
"""
from __future__ import annotations

import json
import re

# ============================================================
# 一、系统接管：完全重复的机械判定
# ============================================================
_PUNCT = re.compile(r"[\s，。、；：！？""''（）()\[\]【】《》\-—~·.,;:!?\"']")
_FILLER = ["那么", "然后", "并且", "而且", "以及", "还有", "另外",
           "的", "了", "就", "都", "也", "再", "会", "要", "把", "被"]


def normalize(text: str) -> str:
    """归一化：去空白标点、去虚词，用于判完全重复"""
    t = _PUNCT.sub("", text or "")
    for w in _FILLER:
        t = t.replace(w, "")
    return t


def exact_dup(a: str, b: str) -> tuple[bool, str]:
    """
    判两条是否"完全重复"（信息无损可合并）。

    判据（全部机械，无判断成分）：
      · 归一化后完全相同
      · 或一方是另一方的子串（且较长那条 ≥12 字，避免误判短句）
    """
    na, nb = normalize(a), normalize(b)
    if na == nb:
        return True, "归一化后完全相同"
    if len(na) >= 12 and len(nb) >= 12:
        if na in nb:
            return True, "前者是后者的子集（后者更完整）"
        if nb in na:
            return True, "后者是前者的子集（前者更完整）"
    return False, ""


def pick_keep(texts: list[str]) -> int:
    """完全重复时选主记录：文本最完整的那条（信息最全）"""
    return max(range(len(texts)), key=lambda k: len(normalize(texts[k])))


# ============================================================
# 二、模型判断：解析融合结果
# ============================================================
def parse_merge_output(raw: str) -> dict:
    """解析模型输出的融合结果（纯机械解析）"""
    if not raw:
        return {}
    m = re.search(r"\{[\s\S]*\}", raw)
    if not m:
        return {}
    try:
        d = json.loads(m[0])
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def parse_merge_batch(raw_text: str) -> list[dict]:
    """解析模型输出的整个批次（数组）"""
    if not raw_text:
        return []
    m = re.search(r"\[[\s\S]*\]", raw_text)
    if not m:
        return []
    try:
        d = json.loads(m[0])
        return d if isinstance(d, list) else []
    except Exception:
        return []


# ============================================================
# 三、系统接管：落盘（只执行，不决定）
# ============================================================
def apply_merge(st, group: dict, verdict: dict) -> dict:
    """
    按模型给的融合结果落盘。

    参数：
      group     {"ep_ids": [...], "texts": [...]}
      verdict   {"same_event": bool, "merged_text": str|null,
                 "keep_ep_id": str, "merge_ep_ids": [...], "reason": str}

    返回动作摘要。
    """
    actions = {"same_event": verdict.get("same_event", False)}

    if not verdict.get("same_event"):
        actions["skipped"] = "模型判定不是同一件事，不融合"
        return actions

    merged_text = (verdict.get("merged_text") or "").strip()
    if not merged_text:
        actions["skipped"] = "模型未给出融合文本"
        return actions

    ep_ids = group.get("ep_ids") or []
    texts = group.get("texts") or []
    keep_id = verdict.get("keep_ep_id") or ""
    merge_ids = verdict.get("merge_ep_ids") or []

    # 系统校验（确定性）：keep_id 必须在组内，merge_ids 也必须在组内且不含 keep
    if keep_id not in ep_ids:
        actions["skipped"] = f"keep_ep_id 不在组内: {keep_id}"
        return actions
    merge_ids = [m for m in merge_ids if m in ep_ids and m != keep_id]
    if not merge_ids:
        actions["skipped"] = "没有需要合并的记录"
        return actions

    # 记录融合前的全部原文（审计，永不丢）
    before = {eid: texts[ep_ids.index(eid)] if eid in ep_ids else ""
              for eid in ep_ids}

    # ① 主记录写入融合后的文本
    st.revise(keep_id, new_text=merged_text)

    # ② 被合并的记录标 superseded_by（不删）
    for mid in merge_ids:
        st.invalidate(mid, superseded_by=keep_id)

    # ③ 审计记录
    rec = {
        "keep_ep_id": keep_id,
        "merge_ep_ids": merge_ids,
        "merged_text": merged_text,
        "before_texts": before,
        "diffs": verdict.get("diffs") or [],
        "reason": verdict.get("reason") or "",
    }
    try:
        st.con.execute("INSERT OR REPLACE INTO meta VALUES (?,?)",
                       (f"merge:{keep_id}",
                        json.dumps(rec, ensure_ascii=False)))
        st.con.commit()
    except Exception:
        pass

    # ④ 失效改变了有效集合，必须清向量缓存（E-133 的教训）
    st.invalidate_vector_cache()

    actions["kept"] = keep_id
    actions["merged"] = merge_ids
    actions["merged_text"] = merged_text
    return actions


def log_exact_merge(st, keep_id: str, merge_ids: list[str],
                    texts: dict, reason: str) -> None:
    """完全重复直接合并的审计记录"""
    rec = {
        "keep_ep_id": keep_id,
        "merge_ep_ids": merge_ids,
        "merged_text": texts.get(keep_id, ""),
        "before_texts": texts,
        "diffs": [],
        "reason": reason,
        "mode": "exact",
    }
    try:
        st.con.execute("INSERT OR REPLACE INTO meta VALUES (?,?)",
                       (f"merge:{keep_id}", json.dumps(rec, ensure_ascii=False)))
        st.con.commit()
    except Exception:
        pass
