"""E-454 检查所有被改路径是否指向正确位置

E-453 教训：
  我把 MODEL_DIR 改成 `os.path.dirname(abspath(__file__))`（指向 cosmos_mem/）
  但模型实际在 项目根/models/bge-small-zh
  → 层级错了，导致 7 项回归失败

本脚本系统性检查：
  对每个含 `os.path.abspath(__file__)` 的文件，
  算出替换后的实际值，看它指向的目录是否存在。

这类错误（改对了语法、改错了语义）最难发现，必须逐个验证。
"""
import os
import re
import json
import subprocess

ROOT = r"C:\Users\xuee\Desktop\肥鱼之心"

print("=" * 74)
print("E-454 验证所有路径替换的实际指向")
print("=" * 74)

r = subprocess.run(["git", "ls-files"], cwd=ROOT,
                   capture_output=True, text=True, encoding="utf-8")
files = [f for f in r.stdout.split("\n") if f.strip()]

# 找所有含 abspath(__file__) 的行
PAT = re.compile(r"os\.path\.(?:dirname\()*os\.path\.abspath\(__file__\)")
hits = []
for f in files:
    if not f.endswith(".py"):
        continue
    p = os.path.join(ROOT, f)
    if not os.path.exists(p):
        continue
    try:
        text = open(p, encoding="utf-8", errors="replace").read()
    except Exception:
        continue
    for i, line in enumerate(text.split("\n"), 1):
        if "abspath(__file__)" in line:
            hits.append((f, i, line.strip()))

print(f"\n含 abspath(__file__) 的行: {len(hits)}")
print(f"涉及文件: {len(set(h[0] for h in hits))}")

# ---------- 关键检查：这些路径拼接后是否指向存在的目录 ----------
print(f"\n{'='*74}")
print("检查实际指向")
print("=" * 74)

problems = []
for f, line, code in hits:
    # 只关心带 os.path.join(..., "xxx") 的目录拼接
    m = re.search(r'os\.path\.join\([^,]*abspath\(__file__\)[^)]*\)', code)
    if not m:
        continue
    # 提取拼接的相对片段
    seg = re.search(r'"([^"]+)"', code[m.end():] if m.end() < len(code) else "")
    # 简化：直接检查该文件所在目录能否按层级找到 models/ 或 results/
    filedir = os.path.dirname(os.path.join(ROOT, f))
    depth = f.count("/")

    # 判断这个文件"应该"用几级上溯
    # proto/xxx.py → 上1级到 proto/，上2级到根
    # proto/cosmos_mem/xxx.py → 上1级 cosmos_mem，上2级 proto，上3级根
    n_ups = len(re.findall(r"os\.path\.dirname\(", code))
    target = filedir
    for _ in range(n_ups):
        target = os.path.dirname(target)

    # 看代码里 join 的下一段是什么
    after = code[code.find("abspath(__file__)"):]
    next_seg = re.findall(r'"([^"]+)"', after)
    if next_seg:
        candidate = os.path.join(target, next_seg[0])
        exists = os.path.exists(candidate)
        if not exists:
            problems.append({
                "file": f, "line": line,
                "n_ups": n_ups,
                "resolved_to": target,
                "next_seg": next_seg[0],
                "candidate": candidate,
                "exists": False,
            })

print(f"指向不存在路径的: {len(problems)}")
for it in problems[:20]:
    print(f"\n  {it['file']}:{it['line']}")
    print(f"    上溯 {it['n_ups']} 级 → {it['resolved_to']}")
    print(f"    拼接 '{it['next_seg']}' → {it['candidate']}")
    print(f"    ❌ 不存在")

# ---------- 重点检查已知的几个 ----------
print(f"\n{'='*74}")
print("重点检查")
print("=" * 74)

KEY = [
    ("proto/cosmos_mem/store_v1.py", "models/bge-small-zh"),
    ("proto/cosmos_mem/rerank_v1.py", "models/bge-reranker"),
]
for f, expect in KEY:
    p = os.path.join(ROOT, f)
    if not os.path.exists(p):
        continue
    text = open(p, encoding="utf-8", errors="replace").read()
    for i, line in enumerate(text.split("\n"), 1):
        if "MODEL_DIR" in line or "models" in line.lower():
            print(f"  {f}:{i}")
            print(f"    {line.strip()[:100]}")

out = {
    "experiment": "E-454",
    "n_hits": len(hits),
    "n_files": len(set(h[0] for h in hits)),
    "n_problems": len(problems),
    "problems": problems[:30],
}
p = os.path.join(ROOT, "proto", "results", "e454_path_verify.json")
with open(p, "w", encoding="utf-8") as f:
    json.dump(out, f, ensure_ascii=False, indent=1)
print(f"\n写入 {p}")
