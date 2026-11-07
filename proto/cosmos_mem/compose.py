"""
compose.py —— 知识册装配流程（程序合并 + 模型重构）

用户指出的架构问题（2026-11-07）：
  "这种随笔到手册书籍，应该是流程性的，而不应该是每一个东西
   都需要大模型真的从头到尾自己重新生成。
   一种结构是：模型每一次生成的是这些随笔（记录的自己觉得可以记录的东西），
   这些东西积累到一定量之后，模型不需要去处理，只需要去调用那个程序，
   这个程序直接把这些东西合并成一个册子，或者说一个类似于书籍的东西，
   之后模型再对这个册子或者书籍这个完整的东西进行重新重构和梳理。
   也就是说，要避免模型这种非必要的上下文整理。"

架构（三段式，职责严格分离）：

  ┌─────────────────────────────────────────────┐
  │ 阶段 1：模型写随笔 scrap                     │
  │   一次一条，便宜，不需要读其他内容            │
  └─────────────────────────────────────────────┘
                    ↓ 攒够 min_items 条
  ┌─────────────────────────────────────────────┐
  │ 阶段 2：程序机械合并（**零模型上下文**）       │
  │   compile_digest()：                          │
  │     · 按 topic 分组                           │
  │     · 纯文本拼接 + 编号 + 去重                  │
  │     · 生成"原始册子" digest                   │
  │   这一步不需要模型参与，纯 SQL + 字符串操作     │
  └─────────────────────────────────────────────┘
                    ↓ 得到完整册子
  ┌─────────────────────────────────────────────┐
  │ 阶段 3：模型对册子做**一次**重构              │
  │   compose_manual()：                          │
  │     · 输入：完整册子（已合并，不是 N 条原始件）│
  │     · 输出：体系化的手册（章节 + 目录）        │
  │   只有这一步用模型，且只用一次                 │
  └─────────────────────────────────────────────┘

为什么这样省：
  · 阶段 1：每条随笔单独生成，不需要重新读历史
  · 阶段 2：程序做，零 token
  · 阶段 3：只读一次合并后的册子，而不是 N 条原文

对比原方案（模型读 N 条原文逐条整理）：
  N=30 时，原方案要读 30 条 × 平均 200 字 = 6000 字
  新方案读一次册子 ≈ 同样字数但**只读一次**，
  且合并在程序侧完成，不需要模型反复对照
"""
from __future__ import annotations

import json
import re
import time

import notebook as _nb


# ============================================================
# 阶段 2：程序机械合并（零模型参与）
# ============================================================

def compile_digest(st, topic: str = "", tier: str = "scrap",
                   min_items: int = 3, max_chars: int = 40000) -> dict:
    """
    把同主题的多条随笔机械合并成一本"原始册子"。

    **完全程序化，不调用模型**：
      · 按 topic 筛选
      · 按创建时间排序
      · 编号 + 拼接
      · 相同正文去重（text_hash）

    返回：
      {
        "topic": ...,
        "n_items": N,
        "digest": "完整册子文本",
        "note_ids": [...],
        "chars": ...,
        "duplicates_removed": N,
      }
    """
    _nb._init(st)

    sql = ("SELECT note_id, title, body, topic, text_hash, created_at "
           "FROM notes WHERE invalid_at IS NULL AND tier=?")
    args = [tier]
    if topic:
        sql += " AND topic=?"
        args.append(topic)
    sql += " ORDER BY created_at ASC"

    rows = st.con.execute(sql, args).fetchall()
    if not rows:
        return {"topic": topic, "n_items": 0, "digest": "",
                "note_ids": [], "chars": 0, "duplicates_removed": 0}

    # ---- 去重（按 text_hash）----
    seen = set()
    uniq = []
    dup = 0
    for r in rows:
        h = r[4] or ""
        if h and h in seen:
            dup += 1
            continue
        if h:
            seen.add(h)
        uniq.append(r)

    # ---- 拼接 ----
    parts = []
    items = []
    total = 0
    used_ids = []
    for i, (nid, title, body, tpc, th, cat) in enumerate(uniq, 1):
        seg = f"### {i}. {title}\n\n{body}\n"
        if total + len(seg) > max_chars:
            parts.append(f"\n（后续 {len(uniq) - i + 1} 条因超长未纳入本次合并）\n")
            break
        parts.append(seg)
        total += len(seg)
        used_ids.append(nid)
        # 结构化条目：供后续 assemble_manual 原样组装
        items.append({"idx": len(items) + 1, "title": title, "body": body,
                      "note_id": nid})

    header = f"# {topic or '未分类'}·原始册子\n\n"
    header += f"> 由 {len(used_ids)} 条{tier}自动合并而成（{time.strftime('%Y-%m-%d')}）\n"
    header += f"> 本册子为机器拼接的原始材料，尚未整理成体系。\n\n"
    header += "---\n\n"

    digest = header + "\n".join(parts)

    return {
        "topic": topic,
        "n_items": len(used_ids),
        "digest": digest,
        "items": items,          # ★ 结构化条目（组装时原文原样嵌入）
        "note_ids": used_ids,
        "chars": len(digest),
        "duplicates_removed": dup,
    }


def compile_all_topics(st, tier: str = "scrap", min_items: int = 3) -> list[dict]:
    """
    扫描一遍，找出**够格合并**的主题（程序判定，零模型）。

    判据：该 topic 下未失效的 tier 条目数 >= min_items。
    """
    _nb._init(st)
    rows = st.con.execute(
        "SELECT topic, COUNT(*) FROM notes "
        "WHERE invalid_at IS NULL AND tier=? AND topic != '' "
        "GROUP BY topic HAVING COUNT(*) >= ?",
        (tier, min_items)).fetchall()
    return [{"topic": t, "count": c} for t, c in rows]


# ============================================================
# 阶段 3：模型重构（只做一次）
# ============================================================

def build_compose_prompt(digest: str, topic: str = "") -> dict:
    """
    构造"模型定大纲"的 prompt。

    用户的关键澄清（2026-11-07）：
      "册子混合在一起之后，模型主要处理的是结构的整理和归纳。
       这就相当于我们记录一个事件，让我们记住了很多东西，
       但是把它定成册的时候，实际上里面具体的记录内容可以不用怎么更改。
       定成册主要做的是定制大纲，给它分好类、分好顺序。
       这不是让模型重新生成一整本书，
       而是让模型重新调整里面内容的顺序、对上下文进行链接等优化。"

    → 模型**只输出结构方案**（大纲/归属/顺序/链接），
      **不重写内容**。程序按方案组装，原文一字不改。

    prompt 明确禁止改写正文，只允许编排。
    """
    system = "\n".join([
        "你是一位技术手册编者，负责**编排**，不负责**改写**。",
        "",
        "下面是一本由零散笔记机械合并而成的『原始册子』，每条都有编号。",
        "",
        "你的任务**只有一件**：设计这本册子的**结构**。具体包括：",
        "  1. 定大纲：分成哪些章节（section）",
        "  2. 归类：每条笔记归属到哪一章",
        "  3. 排序：章内条目的先后顺序",
        "  4. 链接：哪些条目之间存在因果/补充/对照关系",
        "",
        "【严格禁止】",
        "  ✗ 不要重写、润色、改写任何一条笔记的原文",
        "  ✗ 不要编造原文没有的内容",
        "  ✗ 不要合并条目（可以标注它们相关，但不要合成一条）",
        "",
        "【允许】",
        "  ✓ 为章节起标题",
        "  ✓ 为每个章节写一句导读（一句话说明这章讲什么）",
        "  ✓ 标注条目之间的关联",
        "",
        "只输出 JSON，不要任何解释：",
        "{",
        '  "title": "这本册子的标题",',
        '  "outline": [',
        '    {"section": "章节标题", "items": [1,3,5],',
        '     "intro": "一句话导读", "order_note": "为什么这个顺序"}',
        "  ],",
        '  "links": [',
        '    {"from": 1, "to": 2, "relation": "问题→解法"}',
        "  ]",
        "}",
        "",
        "要求：所有条目都必须被分配到大纲里（不能遗漏），每条只属于一个章节。",
    ])

    user = f"原始册子（主题：{topic or '未分类'}）：\n\n{digest}"

    return {"system": system, "user": user,
            "digest_chars": len(digest)}


def assemble_manual(st, outline: dict, digest_items: list,
                    topic: str = "", title: str = "") -> str:
    """
    按模型给出的结构方案组装手册正文。

    **原文一字不改**——只是按 outline 重排、加章节标题和导读。
    这是"程序组装"，不是"模型生成"。

    outline: {"title":..., "outline":[{"section","items","intro"}], "links":[...]}
    digest_items: [{"idx":1,"title":...,"body":...}, ...]
    """
    by_idx = {it["idx"]: it for it in digest_items}
    lines = []

    doc_title = title or outline.get("title") or f"{topic or '未分类'}·手册"
    lines.append(f"# {doc_title}\n")

    sections = outline.get("outline") or []
    n_placed = 0
    for si, sec in enumerate(sections, 1):
        sec_title = sec.get("section") or f"第 {si} 章"
        lines.append(f"\n## {si}. {sec_title}\n")
        intro = sec.get("intro")
        if intro:
            lines.append(f"> {intro}\n")
        for idx in (sec.get("items") or []):
            it = by_idx.get(int(idx)) if isinstance(idx, (int, str)) else None
            if not it:
                continue
            n_placed += 1
            lines.append(f"\n### {it['title']}\n")
            lines.append(it["body"].rstrip() + "\n")

    # 未被分配到的条目，附在末尾（不丢失）
    placed_idx = set()
    for sec in sections:
        for idx in (sec.get("items") or []):
            try:
                placed_idx.add(int(idx))
            except Exception:
                pass
    leftover = [it for it in digest_items if it["idx"] not in placed_idx]
    if leftover:
        lines.append("\n## 附录：未归档条目\n")
        for it in leftover:
            lines.append(f"\n### {it['title']}\n")
            lines.append(it["body"].rstrip() + "\n")

    # 关联关系附在最后
    links = outline.get("links") or []
    if links:
        lines.append("\n## 条目关联\n")
        for lk in links[:50]:
            try:
                a = by_idx.get(int(lk.get("from")))
                b = by_idx.get(int(lk.get("to")))
                if a and b:
                    lines.append(f"- {a['title']} → {b['title']}"
                                 f"（{lk.get('relation', '相关')}）\n")
            except Exception:
                continue

    return "".join(lines)


def parse_outline(raw: str) -> dict | None:
    """从模型输出里解析结构方案 JSON"""
    if not raw:
        return None
    s = raw.strip()
    # 去掉可能的 markdown 代码围栏
    s = re.sub(r"^```(?:json)?\s*", "", s)
    s = re.sub(r"\s*```$", "", s)
    # 找到第一个 { 到最后一个 }
    i, j = s.find("{"), s.rfind("}")
    if i < 0 or j <= i:
        return None
    try:
        d = json.loads(s[i:j + 1])
        return d if isinstance(d, dict) else None
    except Exception:
        return None



def finalize_manual(st, topic: str, manual_body: str,
                    source_note_ids: list, title: str = "") -> dict:
    """
    把模型重构的结果写成手册。

    同时：
      · 记录 source_note_ids（可溯源）
      · 原随笔**不删除**，只保持原样（R2），但可以标记"已归档"
    """
    manual = _nb.note_add(
        st, title=title or f"{topic or '未分类'}·手册",
        body=manual_body, tier="manual", topic=topic,
        source_note_ids=source_note_ids, level="")
    return manual


# ============================================================
# 完整流程（供内核命令 / 插件调用）
# ============================================================

def compose_flow(st, ctx=None, topic: str = "", tier: str = "scrap",
                 min_items: int = 3, call_model=None) -> dict:
    """
    执行完整的三段式流程。

    call_model: 模型调用函数 (system, user) -> str
                它应返回**结构方案 JSON**（不是手册正文）。

    关键（用户澄清）：
      模型只定大纲/分类/排序/链接，**不重写内容**；
      正文由 assemble_manual 按结构原样组装。

    返回：
      {
        "stage": "digest_ready" | "manual_created" | "insufficient" | "model_failed",
        "topic": ...,
        "n_items": N,
        "digest": "...",        # 阶段2产物
        "outline": {...},       # 阶段3产物（结构方案）
        "manual": {...} | None, # 组装并保存后的手册
        "prompt": {...},        # 若未调模型，给出待用 prompt
      }
    """
    # ---------- 阶段 2：程序合并 ----------
    d = compile_digest(st, topic=topic, tier=tier, min_items=min_items)
    if d["n_items"] < min_items:
        return {
            "stage": "insufficient",
            "topic": topic,
            "n_items": d["n_items"],
            "need": min_items,
            "message": f"该主题只有 {d['n_items']} 条{tier}，不足 {min_items} 条，暂不合并",
        }

    if not call_model:
        return {
            "stage": "digest_ready",
            "topic": topic,
            "n_items": d["n_items"],
            "digest": d["digest"],
            "items": d["items"],
            "digest_chars": d["chars"],
            "duplicates_removed": d["duplicates_removed"],
            "note_ids": d["note_ids"],
            "prompt": build_compose_prompt(d["digest"], topic),
        }

    # ---------- 阶段 3：模型定大纲（不写正文） ----------
    p = build_compose_prompt(d["digest"], topic)
    raw = call_model(p["system"], p["user"])
    outline = parse_outline(raw)
    if not outline:
        return {
            "stage": "model_failed",
            "topic": topic,
            "digest_chars": d["chars"],
            "message": "模型未返回可解析的结构方案",
            "raw": (raw or "")[:400],
        }

    # ---------- 阶段 3b：程序按结构组装（原文不改） ----------
    manual_body = assemble_manual(st, outline, d["items"], topic=topic)

    manual = finalize_manual(st, topic, manual_body, d["note_ids"],
                             title=outline.get("title") or "")
    return {
        "stage": "manual_created",
        "topic": topic,
        "n_items": d["n_items"],
        "digest_chars": d["chars"],
        "duplicates_removed": d["duplicates_removed"],
        "outline": outline,
        "manual": manual,
        "source_note_ids": d["note_ids"],
        "assembly": "program",   # 明确：正文由程序组装，非模型生成
    }


__all__ = [
    "compile_digest", "compile_all_topics",
    "build_compose_prompt", "assemble_manual", "parse_outline", "finalize_manual", "compose_flow",
]
