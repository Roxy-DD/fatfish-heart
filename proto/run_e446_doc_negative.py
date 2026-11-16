"""E-446 文档回归测试的自检：破坏必须被捕获

E-428 的教训：不能"遍历 0 次就通过"。
一个只会说 ✅ 的回归测试不是保护，是装饰。

本脚本故意把文档改坏，逐条确认 run_e445 真的报错，
最后**无论如何**都还原原文件（try/finally），避免中途异常留下坏文档。

★ 这不是"测试的测试"这种元游戏 —— 生产里已经出现过
  "countToolCalls 恒返回 0、测试却一直通过"的真实事故（E-422）。
"""
from __future__ import annotations

import os
import re
import subprocess
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
DOC = os.path.join(_ROOT, "docs", "78 生命周期图-环管群.md")
TEST = os.path.join(_HERE, "run_e445_lifecycle_doc_reg.py")

CASES = [
    ("数字漂移（tag 数被改小）",
     lambda s: re.sub(r"git tag 总数 \| \d+", "git tag 总数 | 1", s)),
    ("数字漂移（HEAD tag 被改）",
     lambda s: re.sub(r"HEAD 所在 tag \| \S+", "HEAD 所在 tag | E-999", s)),
    ("数字被编造（大于实测）",
     lambda s: re.sub(r"`episodes` 行数 \| \d+", "`episodes` 行数 | 999999", s)),
    ("图里写回 census 数字",
     lambda s: s.replace(
         'ENV["environment_events 表<br/>exec_ok / exec_fail /'
         '<br/>test_pass / test_fail<br/>客观结果，非内部分数"]',
         'ENV["environment_events 表<br/>实查 58 条"]')),
    ("subgraph/end 不配对",
     lambda s: s.replace("    style NET fill:#fff4e6,stroke:#d68c45\n", "", 1)
                .replace("    end\n\n    S1 --> N1", "    S1 --> N1", 1)),
    ("AUTO-GENERATED 结束标记被删",
     lambda s: s.replace("<!-- /AUTO-GENERATED -->", "", 1)),
    ("回归套件数被改",
     lambda s: re.sub(r"= \*\*\d+ 项\*\*", "= **99 项**", s)),
]


def main() -> int:
    print("=" * 70)
    print("E-446 文档回归测试的自检：破坏必须被捕获")
    print("=" * 70)

    if not os.path.exists(DOC):
        print(f"❌ 文档不存在: {DOC}")
        return 1
    orig = open(DOC, encoding="utf-8").read()

    bad = 0
    try:
        for name, mutate in CASES:
            broken = mutate(orig)
            if broken == orig:
                print(f"  ❌ {name}: 变异没生效（用例本身写错了）")
                bad += 1
                continue
            with open(DOC, "w", encoding="utf-8", newline="\n") as f:
                f.write(broken)
            r = subprocess.run([sys.executable, "-u", TEST], cwd=_HERE,
                               capture_output=True, text=True,
                               encoding="utf-8", errors="replace")
            caught = r.returncode != 0
            print(f"  {'✅' if caught else '❌'} {name}: "
                  f"{'被捕获' if caught else '漏过了！'}")
            if caught:
                fails = [l.strip() for l in r.stdout.splitlines()
                         if l.strip().startswith("❌")]
                if fails:
                    print(f"       {fails[0][:100]}")
            else:
                bad += 1
    finally:
        # 无论发生什么，都必须还原
        with open(DOC, "w", encoding="utf-8", newline="\n") as f:
            f.write(orig)

    restored = open(DOC, encoding="utf-8").read() == orig
    print(f"\n已还原原文件: {restored}")
    if not restored:
        print("  ❌ 还原失败！文档可能已被污染")
        bad += 1

    print(f"结果：{len(CASES) - bad + (0 if restored else 0)}/{len(CASES)} 个破坏被捕获")
    return 0 if bad == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
