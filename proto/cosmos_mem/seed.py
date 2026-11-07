"""
seed.py —— 种子记忆加载（系统初始化时自动装载）

设计意图（用户要求 2026-11-07）：
  "记忆初始化后清空了，但以下内容应该是需要保留的：
   1. 项目本身的记忆（这个项目是个什么东西，这个记忆系统是个什么东西）；
   2. 相关的一些记忆钩子；
   3. 一些有用的、有价值的正向且通用性的记忆，能够提高这个记忆系统能力
      以及相关 AI 模型的认知能力，但一定要是通用性的。"

  → 种子记忆是**系统自带的认知基线**，不是用户数据。
  → 初始化（恢复出厂）后，用户私人的记忆清零，种子记忆保留。
  → 全部通用，不含任何个人隐私，可直接随系统分发给他人使用。

种子来源：seed_memory.json（与代码同目录）

用法：
  from seed import load_seeds
  load_seeds(st)            # 幂等：已存在的种子不会重复写入
"""
from __future__ import annotations

import json
import os

_HERE = os.path.dirname(os.path.abspath(__file__))
SEED_PATH = os.path.join(_HERE, "seed_memory.json")

# 种子记忆在 meta 表中的标记前缀
SEED_KEY_PREFIX = "seed:"


def _seed_key(title: str) -> str:
    return SEED_KEY_PREFIX + title


def load_seed_file(path: str | None = None) -> dict:
    """读取种子文件"""
    p = path or SEED_PATH
    with open(p, "r", encoding="utf-8") as f:
        return json.load(f)


def is_seed(st, ep_id: str) -> bool:
    """这条记忆是不是种子记忆"""
    try:
        row = st.con.execute(
            "SELECT val FROM episodes WHERE ep_id=?", (ep_id,)).fetchone()
        if not row:
            return False
        # 用 meta 标记判断
        m = st.con.execute(
            "SELECT key FROM meta WHERE val LIKE ? AND key LIKE 'seed_ep:%'",
            (f'%"{ep_id}"%',)).fetchone()
        return bool(m)
    except Exception:
        return False


def seed_titles(st) -> dict:
    """返回 {title: ep_id} 的种子映射"""
    out = {}
    try:
        rows = st.con.execute(
            "SELECT key, val FROM meta WHERE key LIKE 'seed_ep:%'").fetchall()
        for key, val in rows:
            try:
                d = json.loads(val)
                out[d.get("title", key)] = d.get("ep_id")
            except Exception:
                continue
    except Exception:
        pass
    return out


def load_seeds(st, path: str | None = None, force: bool = False) -> dict:
    """
    装载种子记忆（幂等）。

    force=False：已装过的种子跳过（按 title 判断）
    force=True ：重新写入（用于种子内容更新后刷新）

    返回 {"loaded": n, "skipped": n, "titles": [...]}
    """
    import hook as _hook

    data = load_seed_file(path)
    existing = seed_titles(st)

    loaded, skipped = 0, 0
    titles = []

    for level in ("L0", "L1", "L2"):
        for spec in data.get(level, []):
            title = spec["title"]
            text = spec["text"]
            kind = spec.get("kind", "policy")

            if title in existing and not force:
                skipped += 1
                titles.append(f"{title}(已存在)")
                continue

            # force 模式：先把旧的失效
            if title in existing and force:
                old_id = existing[title]
                # 直接删 meta 里的 hook 记录（_hook.unlock 对 L0 会拒绝，
                # 但"刷新种子"是合法的迁移动操作，必须绕过保护）
                try:
                    st.con.execute("DELETE FROM meta WHERE key=?",
                                   (f"hook:{old_id}",))
                    st.con.commit()
                except Exception:
                    pass
                st.invalidate(old_id)

            try:
                ep = st.add(text, resolve_conflict=False)
            except Exception:
                continue

            # 锁定保护层级
            _hook.lock(st, ep.ep_id, level=level, kind=kind, note=title)

            # 记录种子标记
            try:
                st.con.execute(
                    "INSERT OR REPLACE INTO meta VALUES (?,?)",
                    (f"seed_ep:{title}",
                     json.dumps({"title": title, "ep_id": ep.ep_id,
                                 "level": level}, ensure_ascii=False)))
                st.con.commit()
            except Exception:
                pass

            loaded += 1
            titles.append(f"{title}[{level}]")

    return {"loaded": loaded, "skipped": skipped, "titles": titles}


def seed_stats(st) -> dict:
    """种子记忆统计"""
    titles = seed_titles(st)
    valid = 0
    try:
        for title, eid in titles.items():
            row = st.con.execute(
                "SELECT invalid_at FROM episodes WHERE ep_id=?",
                (eid,)).fetchone()
            if row and row[0] is None:
                valid += 1
    except Exception:
        pass
    return {"total": len(titles), "valid": valid}


def restore_seeds_only(st, path: str | None = None) -> dict:
    """
    恢复出厂：只保留种子记忆，其余全部失效。

    用户要求："记忆初始化后清空了，但[种子内容]应该是需要保留的。"

    注意：R2 —— 只打失效标记，绝不物理删除。
    """
    import hook as _hook

    # 先确保种子齐备
    load_seeds(st, path=path)

    titles = seed_titles(st)
    keep = set(titles.values())

    # 解锁所有非种子的保护，否则失效会被拒绝
    for p in _hook.protected_of(st):
        if p["ep_id"] not in keep:
            # 直接删 meta 记录（L0 的 unlock 会拒绝，这里需要强制）
            try:
                st.con.execute("DELETE FROM meta WHERE key=?",
                               (f"hook:{p['ep_id']}",))
            except Exception:
                pass
    st.con.commit()

    # 失效所有非种子记忆
    rows = st.con.execute(
        "SELECT ep_id FROM episodes WHERE invalid_at IS NULL").fetchall()
    n_inv = 0
    for (eid,) in rows:
        if eid in keep:
            continue
        if st.invalidate(eid):
            n_inv += 1

    return {
        "invalidated": n_inv,
        "seeds_kept": len(keep),
        "stats": st.stats(),
    }
