"""E-467 让回归不产生 results 噪声

问题：
  跑一次全量回归会改动 15 个 results/*.json，
  差异只有运行时噪声（游标时间戳、随机 ep_id、环境事件计数），
  测试结论没变。但每次提交都会带上这些无意义 diff。

  实测：一次 `iterate.py check` 后 15 个文件变 M（modified）。

修法：
  iterate.py 注入 FFH_NO_WRITE=1，
  各脚本检测到它时跳过写盘（仍打印结论，只是不落文件）。

  单独跑脚本时（不带该环境变量）照常写盘，方便留证据。

实施：
  这 15 个脚本的写盘模式高度一致：
      p = os.path.join(_HERE, "results", "xxx.json")
      with open(p, "w", encoding="utf-8") as f:
          json.dump(out, f, ensure_ascii=False, indent=1)
      print(f"写入 results/xxx.json")

  统一改成：
      if os.environ.get("FFH_NO_WRITE") == "1":
          print("（FFH_NO_WRITE=1，跳过写盘）")
      else:
          ... 原写盘逻辑 ...

  ★ 每个文件改完立刻 ast.parse，失败就回滚该文件。
"""
import ast
import os
import re
import json

HERE = r"C:\Users\xuee\Desktop\肥鱼之心\proto"

# 需要处理的文件（从 git status 里得到的 15 个）
TARGETS = [
    "run_e252_silent_regression.py",
    "run_e29_lifecycle.py",
    "run_e361_chain_reg.py",
    "run_e377_criteria.py",
    "run_e378_same_turn.py",
    "run_e399_env_reg.py",
    "run_e400_verify.py",
    "run_e408_hygiene.py",
    "run_e418_link_ratio.py",
    "run_e420_field_level.py",
    "run_e421_r4.py",
    "run_e424_toolcalls.py",
    "run_e435_r4_invariant.py",
    "run_e438_fingerprint.py",
    "run_e439_r3_scores.py",
]

print("=" * 72)
print("E-467 让回归不产生 results 噪声")
print("=" * 72)

changed = []
skipped = []
failed = []

for fn in TARGETS:
    p = os.path.join(HERE, fn)
    if not os.path.exists(p):
        skipped.append((fn, "文件不存在"))
        continue

    text = open(p, encoding="utf-8", errors="replace").read()

    if "FFH_NO_WRITE" in text:
        skipped.append((fn, "已有 FFH_NO_WRITE"))
        continue

    # 匹配写盘块：
    #   with open(p, "w", encoding="utf-8") as f:
    #       json.dump(out, f, ...)
    # 前面一行通常是 p = os.path.join(...)
    pat = re.compile(
        r'^(?P<indent>[ \t]*)with open\((?P<var>[^,]+),\s*"w",\s*'
        r'encoding="utf-8"\) as (?P<fh>\w+):\n'
        r'(?P<body>(?:(?P=indent)[ \t]+.*\n)+)',
        re.M)

    m = pat.search(text)
    if not m:
        skipped.append((fn, "未找到写盘块"))
        continue

    indent = m.group("indent")
    block = m.group(0)
    # 把原块缩进一级放进 else 分支
    reindented = "\n".join(
        ("    " + ln if ln.strip() else ln)
        for ln in block.rstrip("\n").split("\n"))

    guard = (
        f'{indent}if os.environ.get("FFH_NO_WRITE") == "1":\n'
        f'{indent}    print("（FFH_NO_WRITE=1，跳过写盘）")\n'
        f'{indent}else:\n'
        + reindented + "\n"
    )

    new_text = text[:m.start()] + guard + text[m.end():]

    # 必须确认 os 已导入
    if not re.search(r"^import os\b|^from os import", new_text, re.M):
        if not re.search(r"^import .*\bos\b", new_text, re.M):
            new_text = "import os\n" + new_text

    try:
        ast.parse(new_text)
    except SyntaxError as e:
        failed.append((fn, f"line {e.lineno}: {e.msg}"))
        continue

    open(p, "w", encoding="utf-8", newline="\n").write(new_text)
    changed.append(fn)
    print(f"  ✅ {fn}")

print(f"\n修改 {len(changed)} 个")
if skipped:
    print(f"跳过 {len(skipped)} 个:")
    for fn, why in skipped:
        print(f"  · {fn}: {why}")
if failed:
    print(f"失败 {len(failed)} 个:")
    for fn, why in failed:
        print(f"  ❌ {fn}: {why}")

# ---------- 验证 ----------
print(f"\n{'='*72}")
print("验证：语法 + 环境变量生效")
print("=" * 72)

syn = 0
for fn in TARGETS:
    p = os.path.join(HERE, fn)
    if not os.path.exists(p):
        continue
    try:
        ast.parse(open(p, encoding="utf-8", errors="replace").read())
    except SyntaxError:
        syn += 1
print(f"语法错误: {syn}")

out = {
    "experiment": "E-467",
    "changed": changed,
    "skipped": [{"file": f, "why": w} for f, w in skipped],
    "failed": [{"file": f, "why": w} for f, w in failed],
    "n_syntax": syn,
}
p = os.path.join(HERE, "results", "e467_no_write.json")
with open(p, "w", encoding="utf-8") as f:
    json.dump(out, f, ensure_ascii=False, indent=1)
print(f"写入 {p}")
