"""
store_v2 的模型精筛接口 —— judge.py

把四个瓶颈收敛后的"规则粗筛 + 模型精筛"两级结构落地。

两个需要模型精筛的语义判断点：
  1. identity_refine：区分"人的身份" vs "物的编号"（E-100/E-101 发现的边界）
  2. vague_refine：判断信息密度（E-96 验证 F1=1.0）

触发条件（都由数据验证过）：
  identity：规则命中 IDENTIFIER+BOUND（编号类，可能误判为物）
  vague：无锚点 + 有模糊表态词（E-98，召回 1.0）

调用方式：调用方（真正的 LLM）提供判断结果，本模块只负责触发与缓存。
这样"记忆系统"与"模型"解耦——符合红线 R1。
"""
from __future__ import annotations

import re


# ---------- 触发条件 ----------
VAGUE_WORDS = ["还行", "再讨论", "再想想", "再看看", "到时候", "再说", "可能",
               "差不多", "看情况", "先做着", "再说吧", "需要再", "反正", "总体上",
               "觉得可以", "还没想清楚", "完善一下", "进一步", "回头"]

# 编号类标识符（可能指"物"而非"人"，需模型精筛）
OBJECT_IDENTIFIER = re.compile(
    r"(工牌编号|取件码|订单号|保单号|快递单号|车牌号|门牌号)")


def needs_identity_refine(text: str) -> bool:
    """是否需要用模型精筛"身份 vs 物"的边界"""
    return bool(OBJECT_IDENTIFIER.search(text or ""))


def needs_vague_refine(text: str) -> bool:
    """是否需要用模型精筛"信息密度"（E-98 触发条件）"""
    t = text or ""
    # 有具体锚点 → 不调
    if re.search(r"\d{2,}|\d{1,2}月\d{1,2}|\d{4}[-/年]", t):
        return False
    if re.search(r"([\u4e00-\u9fa5]{2,6}(?:路|街|巷|道)|医院|学校|公司|幼儿园|"
                 r"[A-Z][a-z]+)", t):
        return False
    return any(w in t for w in VAGUE_WORDS)


class ModelRefiner:
    """
    模型精筛缓存层。
    调用方（真实 LLM）通过 `set_result` 写入判断结果，
    `get_result` 读取缓存。内存缓存，进程退出即失效（可重算）。
    """

    def __init__(self):
        self._cache = {}   # (kind, text_hash) -> bool

    def _key(self, kind, text):
        import hashlib
        h = hashlib.md5(text.encode()).hexdigest()
        return (kind, h)

    def set_result(self, kind, text, verdict: bool):
        self._cache[self._key(kind, text)] = bool(verdict)

    def get_result(self, kind, text):
        return self._cache.get(self._key(kind, text))

    def has_result(self, kind, text):
        return self._key(kind, text) in self._cache
