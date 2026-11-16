#!/usr/bin/env python
"""E-475 贡献入口完整性回归

为什么这个要有测试：

  这个项目的目的是形成环管群生态。
  外部贡献者的入口就是 issue / PR 模板。

  模板缺失或格式坏的后果：
    · 别人想反馈，打开一看是空白表单，随手就关了
    · 收到的 issue 全是"不工作了"，无法定位

  这类东西一旦坏掉，不会有任何报错 —— 只是没人来而已。
  没人来是无法自动检测的，但"入口还在不在"可以。

本测试检查：
  ① 三个 issue 模板存在且 front matter 合法
  ② PR 模板存在
  ③ config.yml 合法且引用的链接可达（本地检查路径存在）
  ④ 模板内容包含关键引导（环境输出、红线检查）
"""
import glob
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

results = []


def check(name, cond, detail=""):
    results.append({"name": name, "ok": bool(cond), "detail": detail})
    print(f"  {'✅' if cond else '❌'} {name}" + (f"  {detail}" if detail else ""))


print("=" * 72)
print("E-475 贡献入口完整性")
print("=" * 72)

# ---------- ① issue 模板 ----------
print("\n【1】Issue 模板")

TPL_DIR = os.path.join(ROOT, ".github", "ISSUE_TEMPLATE")
EXPECTED = {
    "bug.md": ["复现", "doctor.py", "期望"],
    "idea.md": ["问题", "红线"],
    "onward.md": ["来自哪里", "对别人"],
}

for fn, keywords in EXPECTED.items():
    p = os.path.join(TPL_DIR, fn)
    if not os.path.exists(p):
        check(f"{fn} 存在", False, "缺失")
        continue
    c = open(p, encoding="utf-8", errors="replace").read()
    check(f"{fn} 存在", True, f"{len(c)} 字节")

    # front matter
    fm = re.match(r"^---\n(.*?)\n---", c, re.S)
    check(f"  {fn} front matter 合法",
          fm is not None and "name:" in (fm.group(1) if fm else ""),
          "有 name/about" if fm else "缺失或不含 name")

    # 关键引导
    missing = [k for k in keywords if k not in c]
    check(f"  {fn} 含关键引导", len(missing) == 0,
          f"缺 {missing}" if missing else "齐全")

# ---------- ② PR 模板 ----------
print("\n【2】PR 模板")

pr = os.path.join(ROOT, ".github", "pull_request_template.md")
if os.path.exists(pr):
    c = open(pr, encoding="utf-8", errors="replace").read()
    check("pull_request_template.md 存在", True, f"{len(c)} 字节")
    for kw in ("回归", "红线", "数据"):
        check(f"  含「{kw}」引导", kw in c)
else:
    check("pull_request_template.md 存在", False, "缺失")

# ---------- ③ config.yml ----------
print("\n【3】config.yml")

cfg = os.path.join(TPL_DIR, "config.yml")
if os.path.exists(cfg):
    c = open(cfg, encoding="utf-8", errors="replace").read()
    check("config.yml 存在", True)

    # 引用的本地路径必须存在
    refs = re.findall(r"blob/master/([^\s\)\"]+)", c)
    bad_refs = []
    for r in refs:
        local = os.path.join(ROOT, r.split("#")[0])
        if not os.path.exists(local):
            bad_refs.append(r)
    check("  引用的本地文件都存在", len(bad_refs) == 0,
          f"失效: {bad_refs}" if bad_refs else f"{len(refs)} 个链接有效")
else:
    check("config.yml 存在", False, "缺失")

# ---------- ④ YAML 语法 ----------
print("\n【4】YAML 语法")

try:
    import yaml
    bad = []
    for p in glob.glob(os.path.join(ROOT, ".github", "**", "*.yml"),
                       recursive=True):
        try:
            yaml.safe_load(open(p, encoding="utf-8"))
        except Exception as e:
            bad.append((os.path.basename(p), str(e)[:50]))
    check("所有 .yml 可解析", len(bad) == 0,
          f"{len(bad)} 个失败" if bad else "")
    for f, e in bad[:3]:
        print(f"      {f}: {e}")
except ImportError:
    print("  ⚠ 无 pyyaml，跳过")

# ---------- ⑤ 文档可发现性 ----------
print("\n【5】从 README 能到达这些入口")

readme = os.path.join(ROOT, "README.md")
if os.path.exists(readme):
    c = open(readme, encoding="utf-8", errors="replace").read()
    check("README 提到 ONWARD 机制", "onward" in c.lower())
    check("README 提到贡献指南", "CONTRIBUTING" in c)
else:
    check("README 存在", False)

# ---------- 汇总 ----------
print("\n" + "=" * 72)
n_ok = sum(1 for x in results if x["ok"])
print(f"结果：{n_ok}/{len(results)} 通过")
for x in results:
    if not x["ok"]:
        print(f"  ❌ {x['name']}  {x['detail']}")
print("=" * 72)

print("""
  ★ 为什么这个值得测：

    贡献入口坏掉不会有任何报错 —— 只是没人来。
    「没人来」无法自动检测，但「入口还在不在」可以。

    这是把"生态是否还能被进入"变成可验证的东西。
""")

import json
out = {
    "experiment": "E-475",
    "n_pass": n_ok,
    "n_total": len(results),
}
p = os.path.join(HERE, "results", "e475_contrib_entry.json")
with open(p, "w", encoding="utf-8") as f:
    json.dump(out, f, ensure_ascii=False, indent=1)
print(f"写入 {p}")

sys.exit(0 if n_ok == len(results) else 1)
