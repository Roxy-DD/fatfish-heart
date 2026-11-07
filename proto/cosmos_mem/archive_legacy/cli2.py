"""v0.2 CLI —— 用 store2"""
from __future__ import annotations

import argparse
import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)
from store2 import MemoryStore

DEFAULT_DB = os.path.join(_HERE, "mem2.db")


def fmt(ep, score=None):
    L = [f"[{ep.ep_id}]" + (f" score={score}" if score is not None else "")]
    meta = []
    for label, vals in (("时间", ep.time), ("人", ep.person),
                        ("地点", ep.location), ("话题", ep.topic)):
        if vals:
            meta.append(f"{label}:" + "/".join(vals))
    if meta:
        L.append("  " + "  ".join(meta))
    if ep.atmosphere:
        L.append(f"  氛围: {ep.atmosphere}")
    tag = " ⚡" if ep.arousal >= 0.5 else ""
    if ep.importance >= 0.5:
        tag += " ★"
    L.append(f"  情绪: 效价{ep.valence:+.2f} 唤醒{ep.arousal:.2f} "
             f"重要{ep.importance:.2f}({ep.affect_source}){tag}")
    L.append(f"  内容: {ep.raw_text}")
    if ep.invalid_at:
        L.append("  ⚠ 已失效" + (f" (被 {ep.superseded_by} 取代)"
                                 if ep.superseded_by else ""))
    if ep.revision > 1:
        L.append(f"  (第 {ep.revision} 版, 可信度 {ep.confidence:.2f})")
    return "\n".join(L)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=DEFAULT_DB)
    sub = ap.add_subparsers(dest="cmd", required=True)

    a = sub.add_parser("add"); a.add_argument("text")
    a.add_argument("--atmosphere", default=""); a.add_argument("--gist", default="")
    a.add_argument("--explicit", action="store_true")

    q = sub.add_parser("ask"); q.add_argument("query")
    q.add_argument("--top", type=int, default=5); q.add_argument("--all", action="store_true")

    l = sub.add_parser("list"); l.add_argument("--n", type=int, default=20)
    s = sub.add_parser("show"); s.add_argument("ep_id")

    r = sub.add_parser("revise"); r.add_argument("ep_id")
    r.add_argument("--text", default=None); r.add_argument("--conf", type=float, default=None)

    iv = sub.add_parser("invalidate"); iv.add_argument("ep_id"); iv.add_argument("--by", default=None)
    sub.add_parser("stats")
    m = sub.add_parser("migrate"); m.add_argument("target")

    args = ap.parse_args()
    st = MemoryStore(args.db)

    if args.cmd == "add":
        ep = st.add(args.text, atmosphere=args.atmosphere, gist=args.gist,
                    explicit=args.explicit)
        print(f"已写入 {ep.ep_id}"); print(fmt(ep))
    elif args.cmd == "ask":
        res = st.search(args.query, top=args.top, include_invalid=args.all)
        if not res:
            print("（没有找到相关记忆）"); return
        print(f"查询: {args.query}\n命中 {len(res)} 条：\n")
        for ep, sc in res:
            print(fmt(ep, sc)); print()
    elif args.cmd == "list":
        from store2 import COLS
        rows = st.con.execute(
            f"SELECT {','.join(COLS)} FROM episodes ORDER BY created_at DESC LIMIT ?",
            (args.n,)).fetchall()
        for r in rows:
            print(fmt(st._ep(r))); print()
    elif args.cmd == "show":
        ep = st.get(args.ep_id); print(fmt(ep) if ep else "未找到")
    elif args.cmd == "revise":
        ok = st.revise(args.ep_id, new_text=args.text, confidence=args.conf)
        print("已修订" if ok else "未找到")
        if ok: print(fmt(st.get(args.ep_id)))
    elif args.cmd == "invalidate":
        if args.by:
            ok, ne = st.supersede(args.ep_id, args.by)
            print(f"旧记忆已失效，新记忆 {ne.ep_id} 已写入")
        else:
            st.invalidate(args.ep_id); print("已标记失效（数据保留）")
    elif args.cmd == "stats":
        print(json.dumps(st.stats(), ensure_ascii=False, indent=2))
    elif args.cmd == "migrate":
        from store2 import COLS, SCHEMA_VERSION
        tgt = MemoryStore(args.target)
        rows = st.con.execute(f"SELECT {','.join(COLS)} FROM episodes").fetchall()
        n = 0
        for r in rows:
            tgt.con.execute(
                f"INSERT OR REPLACE INTO episodes VALUES ({','.join('?'*(len(r)+1))})",
                tuple(r) + (SCHEMA_VERSION,))
            n += 1
        tgt.con.execute("INSERT INTO ep_fts(ep_fts) VALUES('rebuild')")
        tgt.con.commit()
        same = tgt.count() == st.count()
        print(f"迁移 {n} 条 → {args.target}")
        print("条数校验:", "一致" if same else "不一致")


if __name__ == "__main__":
    main()
