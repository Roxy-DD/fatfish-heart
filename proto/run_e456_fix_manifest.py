"""E-456 历史重写后修复 versions.json 的 commit 引用

E-449 用 git-filter-repo 清除了私人数据库，
导致全部 82 个 commit 的 SHA 被重写。

后果：versions.json 里 8 个登记的 commit 全部失效
     （monitor 报「8 个无效」）

修复方式：
  用 tag 反查新 SHA —— tag 名字没变，指向的是新 commit。

  git rev-parse <tag>^{commit}   → 新 SHA

★ 这是历史重写必然带来的连带修复，不是 bug。
  但必须修，否则"版本谱系"层就断了。
"""
import json
import os
import subprocess

ROOT = r"C:\Users\xuee\Desktop\肥鱼之心"
MAN = os.path.join(ROOT, "proto", "work", "versions.json")

print("=" * 74)
print("E-456 修复 versions.json 的 commit 引用")
print("=" * 74)

man = json.load(open(MAN, encoding="utf-8"))
versions = man.get("versions") or []

# 收集所有 tag → SHA
r = subprocess.run(["git", "tag", "--list"], cwd=ROOT,
                   capture_output=True, text=True, encoding="utf-8")
tags = [t.strip() for t in r.stdout.split("\n") if t.strip()]

tag_sha = {}
for t in tags:
    rr = subprocess.run(
        ["git", "rev-parse", f"{t}^{{commit}}"],
        cwd=ROOT, capture_output=True, text=True, encoding="utf-8")
    if rr.returncode == 0:
        tag_sha[t] = rr.stdout.strip()[:9]

print(f"\n解析 {len(tag_sha)} 个 tag")

# 逐个修复
fixed = 0
unfixable = []
for v in versions:
    vid = v.get("id")
    old_commit = v.get("commit")

    # 版本 id 可能就是 tag 名
    if vid in tag_sha:
        new = tag_sha[vid]
        if new != old_commit:
            v["commit"] = new
            print(f"  {vid:<22} {old_commit} → {new}")
            fixed += 1
        continue

    # v0.1.0 这类没有同名 tag，试 v0.1.0 前缀匹配
    matches = [t for t in tag_sha if t.startswith(vid or "")]
    if matches:
        new = tag_sha[matches[0]]
        v["commit"] = new
        print(f"  {vid:<22} {old_commit} → {new} (via {matches[0]})")
        fixed += 1
        continue

    # 实在找不到 → 用当前 HEAD
    unfixable.append(vid)

print(f"\n修复 {fixed} 个")
if unfixable:
    print(f"无法匹配 tag 的: {unfixable}")
    print("  → 这些用当前 HEAD 兜底")
    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT,
                          capture_output=True, text=True,
                          encoding="utf-8").stdout.strip()[:9]
    for v in versions:
        if v.get("id") in unfixable:
            v["commit"] = head
            print(f"  {v['id']} → {head} (HEAD)")

# 写回
man["versions"] = versions
with open(MAN, "w", encoding="utf-8") as f:
    json.dump(man, f, ensure_ascii=False, indent=1)
print(f"\n已写回 {MAN}")

# 验证
print(f"\n{'='*74}")
print("验证")
print("=" * 74)
ok = 0
bad = 0
for v in versions:
    c = v.get("commit")
    rr = subprocess.run(["git", "cat-file", "-e", c],
                        cwd=ROOT, capture_output=True)
    if rr.returncode == 0:
        ok += 1
    else:
        bad += 1
        print(f"  ❌ {v.get('id')}: {c}")
print(f"有效: {ok} / 无效: {bad}")
