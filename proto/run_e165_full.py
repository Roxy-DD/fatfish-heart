"""
E-165 下载 LoCoMo 全量数据并检查结构

之前 E-154 只保存了 d[0]（第一个对话，conv-26），
实际完整文件 2.7MB，包含全部 10 个对话。

本实验：
  ① 下载完整 locomo10.json
  ② 确认对话数与 QA 总数
  ③ 检查各对话的 session 数与 QA 的 evidence 格式一致性
"""
from __future__ import annotations

import json
import os
import urllib.request

_HERE = os.path.dirname(os.path.abspath(__file__))
TASK = os.path.join(_HERE, "work", "e165")
os.makedirs(TASK, exist_ok=True)
RESULTS = os.path.join(_HERE, "results")

URL = "https://raw.githubusercontent.com/snap-research/locomo/main/data/locomo10.json"
OUT = os.path.join(TASK, "locomo10.json")


def main():
    print("=" * 92)
    print("E-165 LoCoMo 全量数据下载与结构检查")
    print("=" * 92)

    if os.path.exists(OUT) and os.path.getsize(OUT) > 1_000_000:
        print(f"\n已存在: {os.path.getsize(OUT):,} bytes，跳过下载")
    else:
        print(f"\n下载 {URL}")
        req = urllib.request.Request(URL, headers={"User-Agent": "research"})
        with urllib.request.urlopen(req, timeout=180) as r:
            raw = r.read()
        with open(OUT, "wb") as f:
            f.write(raw)
        print(f"  已保存 {len(raw):,} bytes")

    print("\n解析…")
    data = json.load(open(OUT, encoding="utf-8"))

    print(f"  顶层类型: {type(data).__name__}")
    print(f"  对话数: {len(data)}")

    # 结构检查
    total_qa = 0
    total_sessions = 0
    total_turns = 0
    rows = []

    for i, conv in enumerate(data):
        sid = conv.get("sample_id")
        qa = conv.get("qa") or []
        c = conv.get("conversation") or {}

        # 数 session
        sessions = []
        j = 1
        while f"session_{j}" in c:
            if c[f"session_{j}"]:
                sessions.append(j)
            j += 1
        n_turns = sum(len(c[f"session_{k}"]) for k in sessions)

        total_qa += len(qa)
        total_sessions += len(sessions)
        total_turns += n_turns

        # evidence 格式
        ev_forms = set()
        for q in qa[:50]:
            ev = q.get("evidence")
            if isinstance(ev, list):
                ev_forms.add("list")
            elif isinstance(ev, str):
                ev_forms.add("str")
            else:
                ev_forms.add(type(ev).__name__)

        rows.append({
            "i": i, "sample_id": sid, "n_qa": len(qa),
            "n_sessions": len(sessions), "n_turns": n_turns,
            "ev_forms": sorted(ev_forms),
            "speaker_a": c.get("speaker_a"),
            "speaker_b": c.get("speaker_b"),
        })

        print(f"\n  [{i}] {sid}  QA={len(qa)}  sessions={len(sessions)}  "
              f"turns={n_turns}")
        print(f"      speakers: {c.get('speaker_a')} / {c.get('speaker_b')}")
        print(f"      evidence 形式: {sorted(ev_forms)}")

    # ---------- 汇总 ----------
    print("\n" + "=" * 92)
    print("汇总")
    print("=" * 92)
    print(f"\n  对话数: {len(data)}")
    print(f"  QA 总数: {total_qa}")
    print(f"  session 总数: {total_sessions}")
    print(f"  轮次总数: {total_turns}")
    print(f"  平均每对话: {total_qa/len(data):.0f} QA, "
          f"{total_sessions/len(data):.0f} sessions, "
          f"{total_turns/len(data):.0f} turns")

    # evidence 一致性
    all_forms = set()
    for r in rows:
        all_forms.update(r["ev_forms"])
    print(f"\n  evidence 形式（全部对话）: {sorted(all_forms)}")
    if len(all_forms) > 1:
        print(f"  ⚠ 形式不一致，适配层需要处理")

    out = {"experiment": "E-165",
           "n_conversations": len(data),
           "total_qa": total_qa,
           "total_sessions": total_sessions,
           "total_turns": total_turns,
           "ev_forms": sorted(all_forms),
           "conversations": rows}
    with open(os.path.join(RESULTS, "e165_locomo_full.json"), "w",
              encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(f"\n写入 {os.path.join(RESULTS, 'e165_locomo_full.json')}")
    print(f"数据路径: {OUT}")


if __name__ == "__main__":
    main()
