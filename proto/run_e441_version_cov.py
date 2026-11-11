"""E-441 版本谱系覆盖率：8/69 登记，缺口在哪

E-440 的测试报出：
  真实 manifest 登记数 = 8
  current = E-235-baseline

但现在 git tag 有 69 个（到 E-439）。

★ 这意味着：**版本谱系的登记严重滞后**。
  如果按《环管群》的「管」（跨生命周期谱系）来看，
  大部分版本节点是不可达的 —— 只知道名字，不知道它的
  commit / 时间 / 迁移关系。

本脚本：
  ① 统计真实覆盖率
  ② 看已登记的是哪些版本（有规律吗）
  ③ 判断是否需要 backfill
  ④ 如果需要，给出方案（不直接执行）

★ 注意：这是只读审计，不改 manifest。
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

print("=" * 74)
print("E-441 版本谱系覆盖率审计")
print("=" * 74)

# ---------- ① 读 manifest ----------
print("\n① manifest 状态")

if not os.path.exists(MANIFEST):
    print(f"   ❌ 不存在: {MANIFEST}")
    sys.exit(1)

man = json.load(open(MANIFEST, encoding="utf-8"))
_vers = man.get("versions") or []
# 兼容 list 和 dict 两种结构
if isinstance(_vers, dict):
    versions = _vers
else:
    versions = {}
    for it in _vers:
        if isinstance(it, dict):
            key = it.get("id") or it.get("name")
            if key:
                versions[key] = it
print(f"   登记版本数: {len(versions)}")
print(f"   current: {man.get('current')}")

print("\n   已登记的：")
for name, info in versions.items():
    commit = str(info.get("commit", ""))[:8]
    at = str(info.get("at", ""))[:19]
    print(f"     {name:<28} {commit}  {at}")

# ---------- ② git tag 现状 ----------
print("\n② git tag 现状")

try:
    r = subprocess.run(
        ["git", "tag", "--list", "E-*"],
        cwd=ROOT, capture_output=True, text=True, timeout=30,
        encoding="utf-8")
    tags = sorted(set(x.strip() for x in r.stdout.split("\n")
                      if x.strip()))
except Exception as e:
    print(f"   读取失败: {e}")
    tags = []

print(f"   git tag 数: {len(tags)}")

# ---------- ③ 覆盖率 ----------
print("\n③ 覆盖率")

registered = set(versions.keys())
tag_set = set(tags)

covered = tag_set & registered
missing = tag_set - registered
extra = registered - tag_set

print(f"   tag ∩ manifest: {len(covered)}")
print(f"   未登记的 tag:   {len(missing)}")
print(f"   manifest 有但无 tag: {len(extra)}")

if tag_set:
    rate = len(covered) / len(tag_set) * 100
    print(f"\n   覆盖率: {rate:.1f}%")

if missing:
    print(f"\n   未登记的 tag（前 20 个）:")
    for t in sorted(missing)[:20]:
        print(f"     {t}")

if extra:
    print(f"\n   manifest 有但无对应 tag:")
    for t in sorted(extra):
        print(f"     {t}")

# ---------- ④ 已登记的有什么规律 ----------
print("\n④ 已登记版本的规律")

if versions:
    # 看是否集中在某段时间
    times = []
    for name, info in versions.items():
        at = info.get("at")
        if at:
            times.append((name, at))
    times.sort(key=lambda x: str(x[1]))
    if times:
        import time as _t
        t0 = _t.strftime("%Y-%m-%d %H:%M", _t.localtime(float(times[0][1])))
        t1 = _t.strftime("%Y-%m-%d %H:%M", _t.localtime(float(times[-1][1])))
        print(f"   时间跨度: {t0} ~ {t1}")

    # 是否都是"baseline"类
    baselines = [n for n in versions if "baseline" in n.lower()]
    print(f"   含 baseline 的: {len(baselines)} / {len(versions)}")
    if baselines:
        for b in baselines:
            print(f"     {b}")

# ---------- ⑤ 判断 ----------
print("\n⑤ 判断：需要 backfill 吗")

print(f"""
   现状：
     git tag:     {len(tags)} 个
     manifest:    {len(versions)} 个
     覆盖率:      {len(covered)/max(1,len(tag_set))*100:.1f}%

   ★ 覆盖率低意味着什么：

     按《环管群》的「管」（跨生命周期谱系）定义，
     每个生命周期节点应该记：commit、时间、迁移关系。
     未登记的版本只有 tag 名，没有 commit 和时间的绑定。

     后果：
       · 无法从版本名反查到代码状态
       · 无法做版本间的迁移实验（不知道往哪迁）
       · E-440 已有的 backfill 机制可以补

   ★ 但也可能**不需要全补**：

     不是每个 E-XXX 都值得成为一个"版本节点"。
     有些只是修个小 bug，有些是结构性变更。
     全补会让 manifest 变成 tag 的镜像，失去"精选"的意义。

   ★ 判断依据：
     看「这个版本是否代表一次能力变化」。
       · 能力变了（如 R1 从部分到全部）→ 值得登记
       · 只修 bug、只加测试 → 不必单独成节点
""")

# ---------- ⑥ 建议 ----------
print("\n⑥ 建议")

# 找出能力发生变化的版本（从 commit message 里看）
print("   从 commit message 里找能力变化标记：")
try:
    r = subprocess.run(
        ["git", "log", "--oneline", "-80"],
        cwd=ROOT, capture_output=True, text=True, timeout=30,
        encoding="utf-8")
    lines = [x for x in r.stdout.split("\n") if x.strip()]

    # 关键词：无损/红线/能力/新增/实现
    key_versions = []
    for line in lines:
        for kw in ("无损", "红线", "实现", "新增", "升级"):
            if kw in line:
                key_versions.append(line[:100])
                break

    print(f"   找到 {len(key_versions)} 条含能力变化关键词的提交：")
    for kv in key_versions[:10]:
        print(f"     {kv}")
except Exception as e:
    print(f"   读取 git log 失败: {e}")

print(f"""
   ★ 我的建议（不执行，等你裁定）：

     方案 A：全量 backfill（用 E-440 的 backfill 机制）
       优点：谱系完整，任何版本都可查
       缺点：manifest 变成 tag 镜像，失去精选意义

     方案 B：只补关键节点（推荐）
       标准：这个版本是否改变了系统的**能力边界**
       例如：
         · R1 从"3 张表"到"10 张表 + 逐字段"
         · R2 从"比行数"到"ep_id 指纹"
         · 生命周期从"不能续跑"到"能续跑"
       这些是能力变化，值得登记。
       只加测试、只修文档的不必。

     方案 C：不补，接受现状
       理由：manifest 主要用途是"迁移实验"，
             实验只需要少数几个稳定锚点，
             不需要每个版本都能迁。

   ★ 我倾向 B，但这条涉及你对「版本谱系要多完整」的判断，
     所以先报告，不直接做。
""")

out = {
    "experiment": "E-441",
    "n_tags": len(tags),
    "n_registered": len(versions),
    "n_covered": len(covered),
    "n_missing": len(missing),
    "coverage_rate": round(len(covered) / max(1, len(tag_set)) * 100, 1),
    "missing_sample": sorted(missing)[:20],
    "current": man.get("current"),
}
p = os.path.join(_HERE, "results", "e441_version_coverage.json")
with open(p, "w", encoding="utf-8") as f:
    json.dump(out, f, ensure_ascii=False, indent=1)
print(f"写入 results/e441_version_coverage.json")
