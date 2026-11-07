"""
E-159 行业标准记忆评测数据集探测

调研结论（2026-11-02）：
  行业事实标准（Mem0/Zep/MemMachine 都在这上面发布数字）：
    ① LoCoMo（ACL 2024）— 10 段超长对话，~7k QA
       · Mem0 报 91.6 F1，Zep 报 75.14 J
       · 数据仅 ~3MB，最适合快速验证
       · 度量：F1 / BLEU-1 / LLM judge
    ② LongMemEval（ICLR 2025）— 500 题，5 个能力轴
       · 度量：LLM judge（我已跑过 37 题）
    ③ MemoryAgentBench（ICLR 2026）— 4 个轴，含 **Conflict Resolution**
       · 正是我们的五类关系处理的强项
       · 度量：substring exact match + accuracy

本实验：探测哪个能拿到，作为下一阶段的对标基准。
"""
from __future__ import annotations

import json
import os
import urllib.request

_HERE = os.path.dirname(os.path.abspath(__file__))
TASK = os.path.join(_HERE, "work", "e159")
os.makedirs(TASK, exist_ok=True)
RESULTS = os.path.join(_HERE, "results")

PROBES = [
    # ---- LoCoMo（最小，优先）----
    ("LoCoMo GitHub raw (locomo10.json)",
     "https://raw.githubusercontent.com/snap-research/locomo/main/data/locomo10.json"),
    ("LoCoMo HF Percena/locomo-mc10",
     "https://datasets-server.huggingface.co/rows"
     "?dataset=Percena%2Flocomo-mc10&config=default&split=train&offset=0&length=2"),

    # ---- LongMemEval 官方 ----
    ("LongMemEval HF xiaowu0162",
     "https://datasets-server.huggingface.co/rows"
     "?dataset=xiaowu0162%2Flongmemeval&config=default&split=train&offset=0&length=2"),

    # ---- MemoryAgentBench ----
    ("MemoryAgentBench HF ai-hyz",
     "https://datasets-server.huggingface.co/rows"
     "?dataset=ai-hyz%2FMemoryAgentBench&config=default&split=train&offset=0&length=2"),

    # ---- MemBench ----
    ("MemBench GitHub",
     "https://raw.githubusercontent.com/import-myself/Membench/main/README.md"),
]


def probe(name, url, timeout=30):
    print(f"\n[{name}]")
    print(f"  {url[:100]}")
    try:
        req = urllib.request.Request(url, headers={
            "User-Agent": "Mozilla/5.0 (research)"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read()
            size = len(raw)
            print(f"  HTTP {r.status}  {size:,} bytes")
            if size < 200:
                print(f"  内容: {raw[:150]}")
                return {"ok": False, "size": size, "reason": "too small"}

            # 尝试解析
            try:
                d = json.loads(raw.decode("utf-8"))

                if isinstance(d, dict) and "rows" in d:
                    n = len(d["rows"])
                    print(f"  ✅ HF rows={n}")
                    if n:
                        r0 = d["rows"][0].get("row", {})
                        print(f"  字段: {list(r0.keys())[:14]}")
                        return {"ok": True, "kind": "hf_rows",
                                "n": n, "fields": list(r0.keys())[:14],
                                "sample": r0}

                elif isinstance(d, list):
                    print(f"  ✅ JSON list, len={len(d)}")
                    if d and isinstance(d[0], dict):
                        print(f"  字段: {list(d[0].keys())[:14]}")
                        return {"ok": True, "kind": "json_list",
                                "n": len(d), "fields": list(d[0].keys())[:14],
                                "sample": d[0]}
                    return {"ok": True, "kind": "json_list", "n": len(d)}

                elif isinstance(d, dict):
                    print(f"  ✅ JSON dict, keys={list(d.keys())[:12]}")
                    return {"ok": True, "kind": "json_dict",
                            "fields": list(d.keys())[:12]}

            except json.JSONDecodeError:
                # 可能是 markdown
                txt = raw.decode("utf-8", errors="replace")
                print(f"  ⚠ 非 JSON，前 100 字符: {txt[:100]}")
                return {"ok": True, "kind": "text", "size": size}

    except Exception as e:
        print(f"  ❌ {type(e).__name__}: {str(e)[:90]}")
        return {"ok": False, "error": str(e)[:110]}


def main():
    print("=" * 96)
    print("E-159 行业标准记忆评测数据集探测")
    print("=" * 96)

    results = {}
    for name, url in PROBES:
        r = probe(name, url)
        results[name] = {k: v for k, v in r.items() if k != "sample"}

        # 存样本
        if r.get("sample"):
            fn = name.split()[0].lower().replace("/", "_") + "_sample.json"
            p = os.path.join(TASK, fn)
            with open(p, "w", encoding="utf-8") as f:
                json.dump(r["sample"], f, ensure_ascii=False, indent=1)
            print(f"  样本 → {p}")

    # ---------- 汇总 ----------
    print("\n" + "=" * 96)
    print("汇总")
    print("=" * 96)

    ok = [(n, r) for n, r in results.items() if r.get("ok")]
    bad = [(n, r) for n, r in results.items() if not r.get("ok")]

    print(f"\n  ✅ 可达: {len(ok)}")
    for n, r in ok:
        print(f"    {n[:48]:<50}{r.get('kind', '')}  n={r.get('n', '-')}")

    if bad:
        print(f"\n  ❌ 不可达: {len(bad)}")
        for n, r in bad:
            print(f"    {n[:48]:<50}{r.get('reason') or r.get('error', '')[:40]}")

    # ---------- 建议 ----------
    print("\n" + "=" * 96)
    print("下一步建议（按行业认可度 × 可达性）")
    print("=" * 96)

    has_locomo = any("locomo" in n.lower() for n, r in ok)
    has_lme = any("longmemeval" in n.lower() for n, r in ok)
    has_mab = any("memoryagentbench" in n.lower() for n, r in ok)

    print(f"\n  LoCoMo 可达: {'✅' if has_locomo else '❌'}")
    print(f"    → 行业事实标准（Mem0 91.6 F1 / Zep 75.14 J 都在这上面）")
    print(f"    → 如果能跑，是最有说服力的对标")
    print(f"\n  LongMemEval 可达: {'✅' if has_lme else '❌'}")
    print(f"    → ICLR 2025，500 题 5 轴（我已跑过替代源的 37 题）")
    print(f"\n  MemoryAgentBench 可达: {'✅' if has_mab else '❌'}")
    print(f"    → ICLR 2026，有 Conflict Resolution 轴（我们强项）")

    out = {"experiment": "E-159", "results": results,
           "available": {"locomo": has_locomo, "longmemeval": has_lme,
                         "memoryagentbench": has_mab}}
    with open(os.path.join(RESULTS, "e159_bench_probe.json"), "w",
              encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(f"\n写入 {os.path.join(RESULTS, 'e159_bench_probe.json')}")


if __name__ == "__main__":
    main()
