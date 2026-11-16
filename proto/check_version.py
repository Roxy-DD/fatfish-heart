#!/usr/bin/env python
"""版本号一致性检查

问题：
  发版后发现 package.json 是 0.4.0，而 git tag 是 v1.0.0。
  两处不一致会让人不知道到底发布的是哪个版本。

本脚本检查所有版本声明点是否一致：
  · dsh-plugin/package.json 的 version
  · 最新的 v* git tag
  · README 里提到的版本号（如果写死了）

用途：
  python proto/check_version.py         # 只检查
  python proto/check_version.py --tag   # 检查并给出打 tag 的命令
"""
import json
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

print("=" * 66)
print("版本号一致性检查")
print("=" * 66)


def git(*args):
    return subprocess.run(["git"] + list(args), cwd=ROOT,
                          capture_output=True, text=True,
                          encoding="utf-8", errors="replace")


problems = []

# ---------- ① package.json ----------
print("\n【1】插件版本")
pkg_path = os.path.join(ROOT, "dsh-plugin", "package.json")
pkg_ver = None
if os.path.exists(pkg_path):
    pkg = json.load(open(pkg_path, encoding="utf-8"))
    pkg_ver = pkg.get("version")
    print(f"   package.json: {pkg_ver}")
else:
    print("   ❌ 找不到 package.json")
    problems.append("package.json 缺失")

# ---------- ② 最新 git tag ----------
print("\n【2】最新 tag")
r = git("tag", "--list", "v*", "--sort=-v:refname")
tags = [t.strip() for t in r.stdout.split("\n") if t.strip()]
if tags:
    latest = tags[0]
    print(f"   最新 v* tag: {latest}")
    print(f"   所有 v* tag: {', '.join(tags[:6])}")
else:
    latest = None
    print("   ⚠ 没有任何 v* tag")

# ---------- ③ 一致性 ----------
print("\n【3】一致性")

if pkg_ver and latest:
    tag_ver = latest.lstrip("v")
    if pkg_ver == tag_ver:
        print(f"   ✅ package.json ({pkg_ver}) == tag ({latest})")
    else:
        print(f"   ❌ package.json ({pkg_ver}) != tag ({latest})")
        problems.append(f"版本不一致: package.json={pkg_ver}, tag={latest}")
elif pkg_ver and not latest:
    print(f"   ⚠ 有版本号 {pkg_ver} 但没打 tag")
    problems.append("缺少 git tag")

# ---------- ④ README 里写死的版本 ----------
print("\n【4】README 版本引用")
readme = os.path.join(ROOT, "README.md")
if os.path.exists(readme):
    c = open(readme, encoding="utf-8", errors="replace").read()
    # 找形如 v1.0.0 / v0.4.0 的版本号
    found = set(re.findall(r"\bv(\d+\.\d+\.\d+)\b", c))
    if found:
        print(f"   README 提到的版本: {', '.join(sorted(found))}")
        if pkg_ver and pkg_ver not in found:
            print(f"   ⚠ README 未提及当前版本 {pkg_ver}")
    else:
        print("   README 未写死版本号（推荐做法）")

# ---------- ⑤ 当前 HEAD 相对 tag 的偏移 ----------
print("\n【5】HEAD 与 tag 的关系")
r = git("describe", "--tags", "--always")
desc = r.stdout.strip()
print(f"   git describe: {desc}")
if "-" in desc and desc.startswith("v"):
    n = desc.split("-")[1]
    print(f"   距最近 tag 有 {n} 个提交")
    print(f"   → 这些提交之后需要再打新 tag（如果是发布级改动）")

# ---------- 汇总 ----------
print("\n" + "=" * 66)
if not problems:
    print("✅ 版本号一致")
    code = 0
else:
    print(f"❌ {len(problems)} 个问题:")
    for p in problems:
        print(f"   · {p}")
    code = 1

if "--tag" in sys.argv and pkg_ver and latest:
    tag_ver = latest.lstrip("v")
    if pkg_ver != tag_ver:
        print(f"\n修复命令（二选一）:")
        print(f"  A. 改 package.json 对齐 tag:  把 version 改成 \"{tag_ver}\"")
        print(f"  B. 改 tag 对齐 package.json:  git tag -a v{pkg_ver} -m ...")

print("=" * 66)
sys.exit(code)
