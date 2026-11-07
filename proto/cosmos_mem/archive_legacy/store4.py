"""
store v0.4 —— 重写链式展开

v0.3 的失败（docs/52）：
  展开时混入了"重要性加成"和"粗 topic 类"，
  结果从夕阳跳到了"老板要出大事"和"电脑密码"——完全无关。
  根本错误：**把"重要性"混进了"相关性"。这两件事必须分开。**

v0.4 的展开只沿两条真实通路（严格按用户描述）：
  路 A 情景相似：向量相似度足够高（且不低于 seed 的一定比例）
  路 B 情绪共振：效价同号 且 唤醒接近 且 两者都够强

并加三个约束（防止越跳越远）：
  1. 相似度衰减阈值：cand_sim >= seed_sim * DECAY
  2. 每跳最多 1-2 条
  3. 总新增条数上限 = hops

场景词不用粗 topic，改抽**具体名词**（夕阳/橘红色/院子），用字面共现判断。
"""
from __future__ import annotations

import json
import os
import re
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)
from store3 import (MemoryStore as BaseStore, Episode, Embedder,
                    extract_elements, COLS, SCHEMA_VERSION, _norm)

# 具体场景名词的抽取：不用分类，直接抓"2-4 字的名词性片段"里的具体物象
SCENE_WORDS = [
    "夕阳", "日落", "晚霞", "橘红", "金黄", "天空", "蓝天", "白云", "星星", "月亮",
    "雨", "雪", "风", "阳光", "光线", "影子", "院子", "小路", "街道", "河边",
    "海", "山", "树", "花", "草地", "窗", "门口", "屋顶", "灯",
    "安静", "热闹", "冷", "暖", "热", "湿润", "干燥",
]
DECAY = 0.75       # 下一跳相似度至少要达到上一跳的 75%


def scene_words(text: str) -> set:
    t = text or ""
    out = set()
    for w in SCENE_WORDS:
        if w in t:
            out.add(w)
    # 颜色 + 名词组合
    for m in re.finditer(r"[橘橙金红粉紫蓝绿灰白]色", t):
        out.add(m.group(0))
    return out


class MemoryStore(BaseStore):
    """在 v0.3 基础上重写 search/expand"""

    def search(self, query, top=5, include_invalid=False,
               expand=False, hops=2):
        seeds = self._search_flat(query, top, include_invalid)
        out = [(ep, sc, 0) for ep, sc in seeds]
        if not expand or not seeds:
            return out

        # 只用最强的 seed 展开（用户描述：看到夕阳→顺着那个夕阳想）
        seed_ep, seed_sc = seeds[0]

        # seed 的向量相似度作为基准
        import numpy as np
        try:
            self._load_vectors()
            sv = Embedder.get().encode(seed_ep.raw_text)[0]
            base_sims = self._vec @ sv
            base_map = {self._vec_ids[i]: float(base_sims[i])
                        for i in range(len(self._vec_ids))}
            seed_sim = base_map.get(seed_ep.ep_id, 1.0)
        except Exception:
            base_map, seed_sim = {}, 1.0

        seen = {ep.ep_id for ep, _, _ in out}
        frontier = [(seed_ep, seed_sim)]
        added = 0

        for hop in range(1, hops + 1):
            nxt = []
            for cur, cur_sim in frontier:
                cands = self._expand_v4(cur, cur_sim, base_map,
                                       include_invalid, k=2)
                for ep, sc in cands:
                    if ep.ep_id in seen:
                        continue
                    if added >= hops * 2:
                        break
                    seen.add(ep.ep_id)
                    out.append((ep, sc, hop))
                    added += 1
                    nxt.append((ep, base_map.get(ep.ep_id, cur_sim * DECAY)))
            frontier = nxt
            if not frontier or added >= hops * 2:
                break
        return out

    def _expand_v4(self, cur: Episode, cur_sim: float, base_map: dict,
                   include_invalid, k=2):
        """
        只沿两条路：情景相似 / 情绪共振。
        **不含重要性、不含粗 topic。**
        """
        w = "" if include_invalid else "AND e.invalid_at IS NULL"
        scored = {}

        # 路 A：情景相似（带衰减阈值）
        for eid, sim in base_map.items():
            if eid == cur.ep_id:
                continue
            if sim < cur_sim * DECAY:      # 太远，不跳
                continue
            scored[eid] = scored.get(eid, 0.0) + sim

        # 路 B：情绪共振（效价同号 + 唤醒接近 + 都够强）
        if abs(cur.valence) >= 0.15 and (cur.arousal or 0) >= 0.15:
            for eid, v, a in self.con.execute(f"""
                    SELECT ep_id, valence, arousal FROM episodes e
                    WHERE ep_id != ? {w}""", (cur.ep_id,)).fetchall():
                if v is None or a is None:
                    continue
                if abs(a) < 0.15:
                    continue
                same = (v * cur.valence) > 0
                close = abs(a - cur.arousal) <= 0.2
                if same and close:
                    scored[eid] = scored.get(eid, 0.0) + 0.5 + 0.5 * cur.arousal

        # 路 C：具体场景词共现（夕阳/橘红色/院子，不是"场景"这类粗类）
        cur_words = scene_words(cur.raw_text + " " + (cur.atmosphere or ""))
        if cur_words:
            for eid, txt, atm in self.con.execute(f"""
                    SELECT ep_id, raw_text, atmosphere FROM episodes e
                    WHERE ep_id != ? {w}""", (cur.ep_id,)).fetchall():
                other = scene_words((txt or "") + " " + (atm or ""))
                common = cur_words & other
                if common:
                    scored[eid] = scored.get(eid, 0.0) + 0.9 * len(common)

        return [(self.get(eid), round(s, 3))
                for eid, s in sorted(scored.items(), key=lambda x: -x[1])[:k]
                if self.get(eid)]
