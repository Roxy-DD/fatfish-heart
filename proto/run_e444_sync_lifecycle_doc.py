"""E-444 同步生命周期图的实测数字（只改 AUTO-GENERATED 区块）

问题：`docs/78 生命周期图-环管群.md` 里的 tag 数、事件数、episodes 行数
是手写的快照。实测发现它们每轮迭代都在漂（80 → 81 → 82），
文档几分钟就过期，而且**过期了没人知道**。

修法：把会漂的数字收敛到文首一个标记块里，由本脚本从真实
git / versions.json / SQLite 读取后重写。图里的 Mermaid 代码
只保留结构关系，不写 census 数字 —— 结构与数据分离。

用法：
  python run_e444_sync_lifecycle_doc.py          # 只显示将写入的内容
  python run_e444_sync_lifecycle_doc.py --apply  # 真正写回文档

只读 git / SQLite，不修改任何业务数据。
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
import subprocess
import sys
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
DOC = os.path.join(_ROOT, "docs", "78 生命周期图-环管群.md")
DB = os.path.join(_HERE, "cosmos_mem", "dsh_mem.db")
MANIFEST = os.path.join(_HERE, "work", "versions.json")

BEGIN = "<!-- AUTO-GENERATED: 由 proto/run_e444_sync_lifecycle_doc.py 生成，勿手改 -->"
END = "<!-- /AUTO-GENERATED -->"


def git(*args: str) -> str:
    r = subprocess.run(["git"] + list(args), cwd=_ROOT, capture_output=True,
                       text=True, encoding="utf-8", errors="replace")
    return r.stdout.strip()


def collect() -> dict:
    tags = [t for t in git("tag").split("\n") if t.strip()]
    try:
        man = json.load(open(MANIFEST, encoding="utf-8"))
        registered = len(man.get("versions") or [])
    except Exception:
        registered = 0
    head_tag = git("describe", "--tags", "--abbrev=0", "HEAD") or "?"

    env_valid = env_all = ep = 0
    try:
        db = sqlite3.connect(DB)
        env_valid = db.execute(
            "SELECT COUNT(*) FROM environment_events "
            "WHERE invalid_at IS NULL").fetchone()[0]
        env_all = db.execute(
            "SELECT COUNT(*) FROM environment_events").fetchone()[0]
        ep = db.execute("SELECT COUNT(*) FROM episodes").fetchone()[0]
        db.close()
    except Exception:
        pass

    # 回归套件条目数：直接数列表，不写死
    n_py = n_cli = 0
    try:
        src = open(os.path.join(_HERE, "iterate.py"), encoding="utf-8").read()
        m = re.search(r"REGRESSION_SUITE = \[(.*?)\n\]", src, re.S)
        n_py = len(re.findall(r'^\s*\("\s*[^"]+"\s*,\s*"[^"]+"', m.group(1), re.M))
        m2 = re.search(r"CLIENT_CHECKS = \[(.*?)\n\]", src, re.S)
        n_cli = len(re.findall(r'^\s*\("\s*[^"]+"\s*,\s*"[^"]+"', m2.group(1), re.M))
    except Exception:
        pass

    return {
        "tags": len(tags),
        "registered": registered,
        "head_tag": head_tag,
        "env_valid": env_valid,
        "env_all": env_all,
        "episodes": ep,
        "n_py": n_py,
        "n_cli": n_cli,
        "n_total": n_py + n_cli,
        "stamp": time.strftime("%Y-%m-%d %H:%M"),
    }


def render(d: dict) -> str:
    rate = d["registered"] / max(1, d["tags"]) * 100
    invalid = d["env_all"] - d["env_valid"]
    env_cell = (f"{d['env_valid']}（总 {d['env_all']}，含 {invalid} 条标记失效）"
                if invalid > 0 else f"{d['env_valid']}（总 {d['env_all']}）")
    t = d["stamp"]
    lines = [
        BEGIN,
        "| 指标 | 实查值 | 测量时刻 |",
        "|---|---|---|",
        f"| git tag 总数 | {d['tags']} | {t} |",
        f"| `versions.json` 登记版本 | {d['registered']} | {t} |",
        f"| 登记覆盖率 | {d['registered']}/{d['tags']} ≈ {rate:.1f}% | {t} |",
        f"| HEAD 所在 tag | {d['head_tag']} | {t} |",
        f"| `environment_events` 有效条数 | {env_cell} | {t} |",
        f"| `episodes` 行数 | {d['episodes']} | {t} |",
        (f"| 回归套件 | {d['n_py']} 项 Python + {d['n_cli']} 项前端/链路"
         f" = **{d['n_total']} 项** | {t} |"),
        END,
    ]
    return "\n".join(lines)


def main() -> int:
    apply = "--apply" in sys.argv
    if not os.path.exists(DOC):
        print(f"❌ 文档不存在: {DOC}")
        return 1

    src = open(DOC, encoding="utf-8").read()
    if BEGIN not in src or END not in src:
        print("❌ 找不到 AUTO-GENERATED 标记块，拒绝改写（避免误伤正文）")
        return 1

    new_block = render(collect())
    pat = re.compile(re.escape(BEGIN) + r".*?" + re.escape(END), re.S)
    n = len(pat.findall(src))
    if n != 1:
        print(f"❌ 标记块出现 {n} 次（应为 1），拒绝改写")
        return 1

    out = pat.sub(lambda _m: new_block, src)
    print(("=" * 70))
    print(f"E-444 生命周期图数字同步（{'APPLY' if apply else 'DRY-RUN'}）")
    print("=" * 70)
    print(new_block)

    if not apply:
        print("\n未写入。加 --apply 生效。")
        return 0

    if out == src:
        print("\n数字无变化，未改动文件（保持 mtime 不变）。")
        return 0

    open(DOC, "w", encoding="utf-8", newline="\n").write(out)
    print(f"\n✅ 已写回 {os.path.relpath(DOC, _ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
