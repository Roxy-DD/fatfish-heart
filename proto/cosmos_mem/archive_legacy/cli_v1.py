"""cosmos_mem v1.0 CLI"""
from __future__ import annotations
import argparse, json, os, sys
_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)
from store_v1 import MemoryStore, COLS

DEFAULT_DB = os.path.join(_HERE, "mem_v1.db")


def fmt(ep, score=None, slot=None):
    tag = ""
    if slot == 1:
        tag = " 📌固定席位"
    L = [f"[{ep.ep_id}]" + (f" score={score}" if score is not None else "") + tag]
    meta = []
    for lb, v in (("时间", ep.time), ("人", ep.person), ("地点", ep.location)):
        if v:
            meta.append(f"{lb}:" + "/".join(v))
    if ep.triggers:
        meta.append("触发:" + "/".join(ep.triggers))
    if meta:
        L.append("  " + "  ".join(meta))
    if ep.atmosphere:
        L.append(f"  氛围: {ep.atmosphere}")
    L.append(f"  cost={ep.cost:.2f} affect={ep.affect_peak:.2f} "
             f"valence={ep.valence:+.2f}")
    L.append(f"  内容: {ep.raw_text}")
    if ep.invalid_at:
        L.append("  ⚠ 已失效")
    if ep.revision > 1:
        L.append(f"  (第 {ep.revision} 版)")
    return "\n".join(L)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=DEFAULT_DB)
    ap.add_argument("--no-semantic", action="store_true")
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("add"); a.add_argument("text")
    a.add_argument("--atmosphere", default=""); a.add_argument("--explicit", action="store_true")
    q = sub.add_parser("ask"); q.add_argument("query")
    q.add_argument("--top", type=int, default=5); q.add_argument("--all", action="store_true")
    q.add_argument("--expand", action="store_true"); q.add_argument("--hops", type=int, default=2)
    q.add_argument("--fixed", type=int, default=0, help="固定席位数")
    l = sub.add_parser("list"); l.add_argument("--n", type=int, default=20)
    s = sub.add_parser("show"); s.add_argument("ep_id")
    r = sub.add_parser("revise"); r.add_argument("ep_id")
    r.add_argument("--text", default=None); r.add_argument("--conf", type=float, default=None)
    iv = sub.add_parser("invalidate"); iv.add_argument("ep_id"); iv.add_argument("--by", default=None)
    sub.add_parser("stats"); sub.add_parser("rebuild")
    m = sub.add_parser("migrate"); m.add_argument("target")

    args = ap.parse_args()
    st = MemoryStore(args.db, use_semantic=not args.no_semantic)

    if args.cmd == "add":
        ep = st.add(args.text, atmosphere=args.atmosphere, explicit=args.explicit)
        print(f"已写入 {ep.ep_id}"); print(fmt(ep))
    elif args.cmd == "ask":
        res = st.search(args.query, top=args.top, include_invalid=args.all,
                        expand=args.expand, hops=args.hops, fixed_slots=args.fixed)
        if not res:
            print("（没有找到）"); return
        print(f"查询: {args.query}\n命中 {len(res)} 条：\n")
        for tup in res:
            ep, sc, sl = tup[0], tup[1], tup[2]
            hop = tup[2] if len(tup) > 2 and tup[2] > 1 else None
            print(fmt(ep, sc, sl)); print()
    elif args.cmd == "list":
        rows = st.con.execute(
            f"SELECT {','.join(COLS)} FROM episodes ORDER BY created_at DESC LIMIT ?",
            (args.n,)).fetchall()
        for r_ in rows:
            print(fmt(st._ep(r_))); print()
    elif args.cmd == "show":
        ep = st.get(args.ep_id); print(fmt(ep) if ep else "未找到")
    elif args.cmd == "revise":
        ok = st.revise(args.ep_id, new_text=args.text, confidence=args.conf)
        print("已修订" if ok else "未找到")
        if ok: print(fmt(st.get(args.ep_id)))
    elif args.cmd == "invalidate":
        if args.by:
            _, ne = st.supersede(args.ep_id, args.by)
            print(f"旧已失效，新 {ne.ep_id} 已写入")
        else:
            st.invalidate(args.ep_id); print("已标记失效")
    elif args.cmd == "stats":
        print(json.dumps(st.stats(), ensure_ascii=False, indent=2))
    elif args.cmd == "rebuild":
        print(f"重建 {st.rebuild_vectors()} 条向量")
    elif args.cmd == "migrate":
        from store_v1 import SCHEMA_VERSION
        tgt = MemoryStore(args.target, use_semantic=False)
        rows = st.con.execute(f"SELECT {','.join(COLS)} FROM episodes").fetchall()
        for r_ in rows:
            tgt.con.execute(
                f"INSERT OR REPLACE INTO episodes VALUES ({','.join('?'*(len(r_)+1))})",
                tuple(r_) + (SCHEMA_VERSION,))
        for v in st.con.execute("SELECT ep_id, dim, vec FROM vectors").fetchall():
            tgt.con.execute("INSERT OR REPLACE INTO vectors VALUES (?,?,?)", tuple(v))
        tgt.con.execute("INSERT INTO ep_fts(ep_fts) VALUES('rebuild')")
        tgt.con.commit()
        print(f"迁移 {len(rows)} 条 → {args.target}")
        print("条数一致" if tgt.count() == st.count() else "不一致")


if __name__ == "__main__":
    main()
