"""E-442 版本谱系覆盖率监控（只读，不自动补）

E-441 审计结果：
  git tag:  78 个
  manifest: 8 个
  覆盖率:   6.4%
  已登记的 8 个全在 2026-11-07 22:32~22:36 四分钟内批量登记
  → 之后 4 天没有任何新登记

这是《环管群》「管」（跨生命周期谱系）的实质缺口。

但"要不要全补"涉及判断：
  · 全补 → manifest 变成 tag 镜像，失去精选意义
  · 只补关键节点 → 需要定义"什么算关键"
  · 不补 → 接受现状，迁移实验只需要少数锚点

所以本测试**只监控，不自动补**：
  ① 记录当前覆盖率
  ② 断言"manifest 没有损坏"（结构完整、commit 有效）
  ③ 断言"最近登记的版本 commit 能被 git 解析"
  ④ 覆盖率下降时告警（说明退化）

★ 不设"覆盖率必须 > X%"的硬门槛，
  因为那会逼我为了过测试而胡乱登记。
"""
import sys
import os
import json
import subprocess
import warnings

warnings.filterwarnings("ignore")
_HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(_HERE)
MANIFEST = os.path.join(_HERE, "work", "versions.json")

results = []


def check(name, cond, detail=""):
    results.append({"name": name, "ok": bool(cond), "detail": detail})
    print(f"  {'✅' if cond else '❌'} {name}" + (f"  {detail}" if detail else ""))


print("=" * 74)
print("E-442 版本谱系覆盖率监控")
print("=" * 74)

# ---------- ① 读 manifest ----------
print("\n【1】manifest 结构完整性")

if not os.path.exists(MANIFEST):
    check("manifest 存在", False, MANIFEST)
    sys.exit(1)

man = json.load(open(MANIFEST, encoding="utf-8"))
check("有 current 字段", "current" in man, str(man.get("current")))
check("有 versions 字段", "versions" in man)

_vers = man.get("versions") or []
check("versions 是列表", isinstance(_vers, list),
      f"{type(_vers).__name__}")

versions = {}
for it in _vers:
    if isinstance(it, dict):
        key = it.get("id") or it.get("name")
        if key:
            versions[key] = it

check("能解析出版本", len(versions) > 0, f"{len(versions)} 个")

# ---------- ② 每个登记的 commit 必须有效 ----------
print("\n【2】登记的 commit 能被 git 解析")

bad_commit = []
for name, info in versions.items():
    commit = info.get("commit")
    if not commit:
        bad_commit.append((name, "无 commit"))
        continue
    try:
        r = subprocess.run(
            ["git", "cat-file", "-e", commit],
            cwd=ROOT, capture_output=True, timeout=10)
        if r.returncode != 0:
            bad_commit.append((name, commit))
    except Exception as e:
        bad_commit.append((name, str(e)))

check("所有登记的 commit 都存在", len(bad_commit) == 0,
      f"{len(bad_commit)} 个无效" if bad_commit else "")
for n, c in bad_commit[:5]:
    print(f"      {n}: {c}")

# ---------- ③ 覆盖率统计（只记录，不设硬门槛）----------
print("\n【3】覆盖率（只记录）")

try:
    r = subprocess.run(["git", "tag", "--list", "E-*"],
                       cwd=ROOT, capture_output=True, text=True,
                       timeout=30, encoding="utf-8")
    tags = set(x.strip() for x in r.stdout.split("\n") if x.strip())
except Exception:
    tags = set()

covered = tags & set(versions.keys())
rate = len(covered) / max(1, len(tags)) * 100

print(f"   git tag:   {len(tags)}")
print(f"   manifest:  {len(versions)}")
print(f"   交集:      {len(covered)}")
print(f"   覆盖率:    {rate:.1f}%")

check("tag 数量合理", len(tags) > 0, f"{len(tags)} 个")
check("manifest 未清空", len(versions) > 0, f"{len(versions)} 个")

# 记录但不强制
if rate < 10:
    print(f"""
   ⚠ 覆盖率 {rate:.1f}% 偏低（但仍不判定失败）

  这是已知的「管」层缺口（E-441 审计）。
  不设硬门槛，因为强行补会让 manifest 变成 tag 镜像。
  是否补齐需要按「能力边界是否变化」来筛选，是判断题不是机械操作。
""")

# ---------- ④ current 必须指向已登记版本 ----------
print("\n【4】current 一致性")

cur = man.get("current")
if cur:
    check("current 指向已登记的版本", cur in versions, str(cur))
else:
    check("current 为空（允许）", True, "空")

# ---------- ⑤ 是否有重复 id ----------
print("\n【5】数据结构健全性")

ids = [it.get("id") or it.get("name") for it in _vers
       if isinstance(it, dict)]
check("没有重复 id", len(ids) == len(set(ids)),
      f"{len(ids)} 个，去重后 {len(set(ids))}")

# 必需字段
missing_fields = []
for it in _vers:
    if not isinstance(it, dict):
        continue
    for f in ("id", "commit", "at"):
        if not it.get(f):
            missing_fields.append((it.get("id"), f))

check("所有版本有 id/commit/at", len(missing_fields) == 0,
      f"{len(missing_fields)} 处缺失" if missing_fields else "")

# ---------- 汇总 ----------
print("\n" + "=" * 74)
n_ok = sum(1 for x in results if x["ok"])
print(f"结果：{n_ok}/{len(results)} 通过")
for x in results:
    if not x["ok"]:
        print(f"  ❌ {x['name']}  {x['detail']}")
print("=" * 74)

print(f"""
  ★ 这个测试的定位（重要）：

    它**只监控，不驱动补齐**。

    对比一下两种设计：
      ❌ 设"覆盖率必须 > 50%" → 我会为了过测试而胡乱登记
      ✅ 只断言"结构没坏、commit 有效、current 一致"
         → 覆盖率低了会提示，但不判失败

    ★ 为什么这样设计：
      覆盖率是**结果指标**，不该当**门禁指标**。
      能当门禁的只有"数据是否损坏"。
      逼结果会催生虚假动作 —— 这正是我在 E-403 学到的。

  ★ 「管」层缺口待办：
    78 个 tag，8 个登记，覆盖率 6.4%。
    补齐与否取决于「什么算版本节点」的定义，
    需要按能力边界变化筛选，是判断题。
""")

out = {
    "experiment": "E-442",
    "n_pass": n_ok,
    "n_total": len(results),
    "n_tags": len(tags),
    "n_registered": len(versions),
    "n_covered": len(covered),
    "coverage_rate": round(rate, 1),
    "bad_commits": len(bad_commit),
    "current": cur,
}
p = os.path.join(_HERE, "results", "e442_version_monitor.json")
with open(p, "w", encoding="utf-8") as f:
    json.dump(out, f, ensure_ascii=False, indent=1)
print(f"写入 results/e442_version_monitor.json")

sys.exit(0 if n_ok == len(results) else 1)
