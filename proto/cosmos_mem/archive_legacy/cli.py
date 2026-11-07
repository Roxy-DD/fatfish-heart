"""
cosmos_mem CLI —— 命令行原型

用法：
  python cli.py add "文本" [--atmosphere 氛围] [--gist 骨架]
  python cli.py ask "查询" [--top 5] [--all]
  python cli.py list [--n 20]
  python cli.py show <ep_id>
  python cli.py revise <ep_id> --text "新文本" [--conf 0.5]
  python cli.py invalidate <ep_id> [--by new_text]
  python cli.py stats
  python cli.py migrate <目标db>   # 迁移到新文件，验证无损

设计目标：先让你能真实用起来，验证"情景 + 情绪 + 可塑性"对不对。
"""
from __future__ import annotations

import argparse
import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)

from store import MemoryStore, estimate_affect, extract_elements

DEFAULT_DB = os.path.join(_HERE, "mem.db")


def fmt_ep(ep, score=None) -> str:
    lines = []
    head = f"[{ep.ep_id}]"
    if score is not None:
        head += f" score={score:.2f}"
    lines.append(head)
    meta = []
    if ep.time:
        meta.append("时间:" + "/".join(ep.time))
    if ep.person:
        meta.append("人:" + "/".join(ep.person))
    if ep.location:
        meta.append("地点:" + "/".join(ep.location))
    if ep.topic:
        meta.append("话题:" + "/".join(ep.topic))
    if meta:
        lines.append("  " + "  ".join(meta))
    if ep.atmosphere:
        lines.append(f"  氛围: {ep.atmosphere}")
    emoji = ""
    if ep.arousal >= 0.5:
        emoji = " ⚡"
    lines.append(f"  情绪: 效价{ep.valence:+.2f} 唤醒{ep.arousal:.2f}"
                 f"({ep.affect_source}){emoji}")
    lines.append(f"  内容: {ep.raw_text}")
    if ep.invalid_at:
        lines.append(f"  ⚠ 已失效" + (f" (被 {ep.superseded_by} 取代)"
                                       if ep.superseded_by else ""))
    if ep.revision > 1:
        lines.append(f"  (第 {ep.revision} 版, 可信度 {ep.confidence:.2f})")
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser(description="cosmos_mem 情景记忆库")
    ap.add_argument("--db", default=DEFAULT_DB)
    sub = ap.add_subparsers(dest="cmd", required=True)

    a = sub.add_parser("add")
    a.add_argument("text")
    a.add_argument("--atmosphere", default="")
    a.add_argument("--gist", default="")

    q = sub.add_parser("ask")
    q.add_argument("query")
    q.add_argument("--top", type=int, default=5)
    q.add_argument("--all", action="store_true", help="包含已失效")

    l = sub.add_parser("list")
    l.add_argument("--n", type=int, default=20)

    s = sub.add_parser("show")
    s.add_argument("ep_id")

    r = sub.add_parser("revise")
    r.add_argument("ep_id")
    r.add_argument("--text", default=None)
    r.add_argument("--conf", type=float, default=None)

    iv = sub.add_parser("invalidate")
    iv.add_argument("ep_id")
    iv.add_argument("--by", default=None, help="用新文本取代它")

    sub.add_parser("stats")

    m = sub.add_parser("migrate")
    m.add_argument("target")

    args = ap.parse_args()
    st = MemoryStore(args.db)

    if args.cmd == "add":
        ep = st.add(args.text, atmosphere=args.atmosphere, gist=args.gist)
        print(f"已写入 {ep.ep_id}")
        print(fmt_ep(ep))

    elif args.cmd == "ask":
        res = st.search(args.query, top=args.top, include_invalid=args.all)
        if not res:
            print("（没有找到相关记忆）")
            return
        print(f"查询: {args.query}")
        print(f"命中 {len(res)} 条：\n")
        for ep, sc in res:
            print(fmt_ep(ep, sc))
            print()

    elif args.cmd == "list":
        rows = st.con.execute(
            "SELECT ep_id,created_at,person,time,location,topic,raw_text,gist,"
            "atmosphere,valence,arousal,affect_source,confidence,invalid_at,"
            "superseded_by,revision,text_hash FROM episodes "
            "ORDER BY created_at DESC LIMIT ?", (args.n,)).fetchall()
        for r in rows:
            print(fmt_ep(st._row_to_ep(r)))
            print()

    elif args.cmd == "show":
        ep = st.get(args.ep_id)
        print(fmt_ep(ep) if ep else "未找到")

    elif args.cmd == "revise":
        ok = st.revise(args.ep_id, new_text=args.text, confidence=args.conf)
        print("已修订" if ok else "未找到")
        if ok:
            print(fmt_ep(st.get(args.ep_id)))

    elif args.cmd == "invalidate":
        if args.by:
            ok, new_ep = st.supersede(args.ep_id, args.by)
            print(f"旧记忆已失效，新记忆 {new_ep.ep_id} 已写入")
        else:
            st.invalidate(args.ep_id)
            print("已标记失效（数据保留）")

    elif args.cmd == "stats":
        print(json.dumps(st.stats(), ensure_ascii=False, indent=2))

    elif args.cmd == "migrate":
        # 无损迁移验证：逐条复制到新文件，比对
        tgt = MemoryStore(args.target)
        rows = st.con.execute(
            "SELECT ep_id,created_at,person,time,location,topic,raw_text,gist,"
            "atmosphere,valence,arousal,affect_source,confidence,invalid_at,"
            "superseded_by,revision,text_hash FROM episodes").fetchall()
        n = 0
        for r in rows:
            tgt.con.execute("""INSERT OR REPLACE INTO episodes VALUES
                (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                tuple(r) + ("cosmos-mem/0.1",))
            n += 1
        tgt.con.execute("INSERT INTO ep_fts(ep_fts) VALUES('rebuild')")
        tgt.con.commit()
        print(f"迁移 {n} 条 → {args.target}")
        print("校验：", "一致" if tgt.count() == st.count() else "不一致")


if __name__ == "__main__":
    main()
