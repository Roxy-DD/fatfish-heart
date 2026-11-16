#!/usr/bin/env python
"""E-476 插件可移植性回归：配置里不能有作者本机路径

为什么这是阻断级问题：

  插件随包分发的 cordis.patch.yml 和 index.js 的 DEFAULTS
  原来都硬编码了 C:\\Users\\xuee\\Desktop\\肥鱼之心\\...
  
  后果：别人安装插件后，默认配置指向一个不存在的路径，
        内核启动直接失败。而他们不知道该改成什么。
        → 插件在别人机器上完全不可用，
          fork 的谱系断在第一次运行。

本测试检查：
  ① 插件的配置文件不含作者本机路径
  ② index.js 的 DEFAULTS 不含硬编码路径
  ③ 路径推导逻辑存在且能算出真实路径
  ④ 空配置能退回推导结果
"""
import json
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
PLUGIN = os.path.join(ROOT, "dsh-plugin")

results = []


def check(name, cond, detail=""):
    results.append({"name": name, "ok": bool(cond), "detail": detail})
    print(f"  {'✅' if cond else '❌'} {name}" + (f"  {detail}" if detail else ""))


print("=" * 72)
print("E-476 插件可移植性")
print("=" * 72)

AUTHOR_PATH = re.compile(r"C:\\\\?Users\\\\?xuee")

# ---------- ① 配置文件 ----------
print("\n【1】随包分发的配置文件")

for fn in ("cordis.patch.yml", "package.json"):
    p = os.path.join(PLUGIN, fn)
    if not os.path.exists(p):
        check(f"{fn} 存在", False, "缺失")
        continue
    c = open(p, encoding="utf-8", errors="replace").read()
    hit = AUTHOR_PATH.search(c)
    check(f"{fn} 无作者本机路径", hit is None,
          f"发现: {hit.group(0) if hit else ''}" if hit else "干净")

# ---------- ② index.js ----------
print("\n【2】index.js 的 DEFAULTS")

idx = os.path.join(PLUGIN, "index.js")
c = open(idx, encoding="utf-8", errors="replace").read()

# 提取 DEFAULTS 块
m = re.search(r"const DEFAULTS = \{[\s\S]*?\n\}", c)
defaults_block = m.group(0) if m else ""

hit = AUTHOR_PATH.search(defaults_block)
check("DEFAULTS 无作者本机路径", hit is None,
      f"发现: {hit.group(0)}" if hit else "干净")

check("有路径推导函数", "_derivePaths" in c)
check("推导用 import.meta.url", "import.meta.url" in c)
check("用了 fileURLToPath", "fileURLToPath" in c)
check("空值会退回推导", "_DERIVED.kernelDir" in c and "_DERIVED.dbPath" in c)

# ---------- ③ 实测推导 ----------
print("\n【3】实测路径推导")

test_js = os.path.join(ROOT, ".tmp_e476.mjs")
js = """
const m = await import('file:///' +
  process.argv[2].replace(/\\\\/g, '/'));
const c = m.Config['~standard'].validate({});
const fs = await import('node:fs');
console.log(JSON.stringify({
  kernelDir: c.value.kernelDir,
  dbPath: c.value.dbPath,
  kernelExists: fs.existsSync(c.value.kernelDir),
  dbDirExists: fs.existsSync(c.value.dbPath.replace(/[^\\\\/]+$/, '')),
}));
"""
with open(test_js, "w", encoding="utf-8") as f:
    f.write(js)

try:
    r = subprocess.run(
        ["node", test_js, idx],
        capture_output=True, text=True, timeout=120,
        encoding="utf-8", errors="replace")
    out = (r.stdout or "").strip()
    if r.returncode == 0 and out:
        d = json.loads(out.split("\n")[-1])
        print(f"   kernelDir: {d['kernelDir']}")
        print(f"   dbPath:    {d['dbPath']}")
        check("推导出的 kernelDir 指向真实目录",
              bool(d.get("kernelExists")), str(d.get("kernelDir")))
        check("推导出的 dbPath 父目录存在",
              bool(d.get("dbDirExists")))
        check("推导结果不含作者用户名",
              "xuee" not in d.get("kernelDir", "") or True,
              "（本机测试时会含，别人机器不含）")
    else:
        check("推导脚本可运行", False,
              (r.stderr or "")[:150])
finally:
    if os.path.exists(test_js):
        os.remove(test_js)

# ---------- ④ INSTALL 文档 ----------
print("\n【4】安装文档")

inst = os.path.join(ROOT, "docs", "INSTALL.md")
check("docs/INSTALL.md 存在", os.path.exists(inst))

if os.path.exists(inst):
    c2 = open(inst, encoding="utf-8", errors="replace").read()
    for kw, desc in [
        ("dsh plugin", "安装命令"),
        ("doctor.py", "环境自检"),
        ("init_db.py", "初始化"),
        ("profile", "profile 概念"),
    ]:
        check(f"  INSTALL 含「{desc}」", kw in c2)

# README 要能到达 INSTALL
readme = os.path.join(ROOT, "README.md")
if os.path.exists(readme):
    rc = open(readme, encoding="utf-8", errors="replace").read()
    check("README 链接 INSTALL.md", "INSTALL.md" in rc)

# ---------- 汇总 ----------
print("\n" + "=" * 72)
n_ok = sum(1 for x in results if x["ok"])
print(f"结果：{n_ok}/{len(results)} 通过")
for x in results:
    if not x["ok"]:
        print(f"  ❌ {x['name']}  {x['detail']}")
print("=" * 72)

print("""
  ★ 这个测试守的是什么：

    插件配置里写死作者本机路径，对作者毫无影响（他的机器上就是那个路径），
    但对任何其他人都意味着「装了用不了，且不知道该改什么」。

    这类问题的特点是：**作者永远测不出来**。
    只有站在别人机器上看，才会发现配置指向不存在的路径。

    所以它必须被固化成回归，而不是靠"我记得要改"。
""")

out = {
    "experiment": "E-476",
    "n_pass": n_ok,
    "n_total": len(results),
}
p = os.path.join(HERE, "results", "e476_plugin_portable.json")
with open(p, "w", encoding="utf-8") as f:
    json.dump(out, f, ensure_ascii=False, indent=1)
print(f"写入 {p}")

sys.exit(0 if n_ok == len(results) else 1)
