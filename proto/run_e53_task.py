"""
E-53 准备作答任务：把各档位题目合并成一份份任务文件，交给独立模型实例作答。
"""
from __future__ import annotations

import json
import os

_HERE = os.path.dirname(os.path.abspath(__file__))
WORK = os.path.join(_HERE, "work", "e53v3")
TASK = os.path.join(_HERE, "work", "e53_task")
os.makedirs(TASK, exist_ok=True)

# 档位 -> 要合并的包前缀
GROUPS = {
    "FULL": ["FULL_head", "FULL_mid", "FULL_tail"],
    "TRUNC": ["TRUNC_head", "TRUNC_mid", "TRUNC_tail"],
    "KEEP": ["KEEP_head", "KEEP_mid", "KEEP_tail"],
}


def main():
    for gname, prefixes in GROUPS.items():
        rows = []
        for p in prefixes:
            for bi in range(3):
                fn = os.path.join(WORK, f"{p}_{bi}.json")
                if os.path.exists(fn):
                    with open(fn, encoding="utf-8") as f:
                        rows.extend(json.load(f))
        # 去重（FULL 三个位置文件内容相同）
        seen, uniq = set(), []
        for r in rows:
            k = (r["id"], r["question"], r["context"])
            if k not in seen:
                seen.add(k)
                uniq.append(r)
        # 只保留作答需要的字段
        out = [{"qid": f"{gname}-{i:03d}", "context": r["context"],
                "question": r["question"]} for i, r in enumerate(uniq)]
        path = os.path.join(TASK, f"{gname}.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(out, f, ensure_ascii=False, indent=1)
        # 同时存答案表（评分用，不给模型看）
        key = {f"{gname}-{i:03d}": {"answer": r["answer"], "id": r["id"],
                                    "pos": r.get("pos")}
               for i, r in enumerate(uniq)}
        with open(os.path.join(TASK, f"{gname}_key.json"), "w", encoding="utf-8") as f:
            json.dump(key, f, ensure_ascii=False, indent=1)
        print(f"{gname}: {len(out)} 题 → {path}")

    print(f"\n任务文件在 {TASK}")


if __name__ == "__main__":
    main()
