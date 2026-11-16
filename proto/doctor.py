#!/usr/bin/env python
"""环境自检（doctor）—— 别人 clone 后第一个该跑的命令

为什么需要它：
  README 写了要装什么，但"装没装对"没法自己确认。
  报错信息往往是 ImportError 或 CUDA 错误，看不出缺失的是什么。

本脚本逐项检查并给出**可执行的修复命令**：
  · Python 版本
  · 核心依赖（numpy / jieba / transformers / torch / onnxruntime）
  · 模型文件是否就位
  · 数据库是否可访问
  · 检索能否真的跑通

用法：
  python proto/doctor.py
  python proto/doctor.py --fix    # 打印修复命令（不自动执行）
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
MEM = os.path.join(HERE, "cosmos_mem")

OK = "✅"
BAD = "❌"
WARN = "⚠ "

results = []


def check(name, ok, detail="", fix=""):
    results.append({"name": name, "ok": ok,
                    "detail": detail, "fix": fix})
    mark = OK if ok else BAD
    print(f"  {mark} {name}" + (f"  {detail}" if detail else ""))
    if not ok and fix:
        print(f"      修复: {fix}")


def warn(name, detail=""):
    print(f"  {WARN}{name}" + (f"  {detail}" if detail else ""))


print("=" * 70)
print("肥鱼之心（Fat Fish Heart）· 环境自检")
print("=" * 70)

# ---------- ① Python 版本 ----------
print("\n【1】Python 版本")
v = sys.version_info
check(f"Python >= 3.10", v >= (3, 10),
      f"当前 {v.major}.{v.minor}.{v.micro}",
      "安装 Python 3.10+（推荐 3.12）")

# ---------- ② 核心依赖 ----------
print("\n【2】核心依赖")

DEPS = [
    ("numpy", "numpy", "pip install numpy"),
    ("jieba", "jieba", "pip install jieba"),
    ("transformers", "transformers", "pip install transformers"),
    ("torch", "torch", "pip install torch"),
]

for mod, pkg, fix in DEPS:
    try:
        m = __import__(mod)
        ver = getattr(m, "__version__", "?")
        check(f"{pkg}", True, ver)
    except ImportError:
        check(f"{pkg}", False, "未安装", fix)

# onnxruntime 二选一
ort_name = None
try:
    import onnxruntime as ort
    ort_name = "onnxruntime"
    providers = ort.get_available_providers()
    gpu = "CUDAExecutionProvider" in providers
    check("onnxruntime", True,
          f"{ort.__version__} ({'GPU' if gpu else 'CPU'})")
    if not gpu:
        warn("未启用 GPU", "会用 CPU 推理，速度慢但可用")
except ImportError:
    check("onnxruntime", False, "未安装",
          "pip install onnxruntime-gpu  （或 pip install onnxruntime）")

# huggingface_hub（下载模型用）
try:
    import huggingface_hub as hh
    check("huggingface-hub", True, hh.__version__)
except ImportError:
    check("huggingface-hub", False, "未安装",
          "pip install huggingface-hub")

# ---------- ③ 模型文件 ----------
print("\n【3】模型文件")

MODELS = [
    ("bge-small-zh", True, "嵌入模型，必需"),
    ("bge-reranker-v2-m3-fp16", False, "精排模型，可选"),
]

for name, required, desc in MODELS:
    p = os.path.join(ROOT, "models", name)
    exists = os.path.isdir(p)
    if exists:
        # 检查关键文件
        has_cfg = os.path.exists(os.path.join(p, "config.json"))
        n_files = len(os.listdir(p))
        if has_cfg and n_files >= 2:
            check(f"models/{name}", True, f"{n_files} 个文件 — {desc}")
        else:
            check(f"models/{name}", False,
                  f"目录存在但内容不全（{n_files} 个文件）",
                  f"重新下载（见 README「先下载模型」）")
    else:
        if required:
            check(f"models/{name}", False, f"缺失 — {desc}",
                  f"huggingface-cli download BAAI/bge-small-zh-v1.5 "
                  f"--local-dir models/bge-small-zh")
        else:
            warn(f"models/{name} 缺失", f"{desc}，不影响基本功能")

# ---------- ④ 数据库 ----------
print("\n【4】数据库")

DB = os.path.join(MEM, "dsh_mem.db")
if os.path.exists(DB):
    size_kb = os.path.getsize(DB) // 1024
    check("dsh_mem.db", True, f"{size_kb} KB")
else:
    warn("dsh_mem.db 不存在",
         "首次运行会自动创建（空库），这是正常的")

# ---------- ⑤ 实测检索 ----------
print("\n【5】实测：能否真的检索")

if not all(r["ok"] for r in results if "models/bge-small-zh" in r["name"]):
    warn("跳过检索测试", "嵌入模型未就位，无法测")
else:
    try:
        sys.path.insert(0, MEM)
        import warnings
        warnings.filterwarnings("ignore")

        import environment as env  # noqa: F401
        from store_v2 import MemoryStore

        # 用临时库，不碰真实记忆
        import tempfile
        tmpdir = tempfile.mkdtemp(prefix="ffh_doctor_")
        tmpdb = os.path.join(tmpdir, "test.db")

        st = MemoryStore(tmpdb)
        ep = st.add("自检测试：肥鱼之心的记忆系统能否正常工作",
                    atmosphere="doctor")
        eid = ep.ep_id if hasattr(ep, "ep_id") else str(ep)

        r = st.search("记忆系统", top=3)
        n = len(r)
        check("写入 + 检索", n > 0, f"写入 1 条，检索到 {n} 条")

        st.con.close()
        import shutil
        shutil.rmtree(tmpdir, ignore_errors=True)
    except Exception as e:
        check("写入 + 检索", False,
              f"{type(e).__name__}: {str(e)[:80]}",
              "上面各项若都通过，请提 issue 附此错误")

# ---------- 汇总 ----------
print("\n" + "=" * 70)
n_ok = sum(1 for r in results if r["ok"])
n_bad = len(results) - n_ok

if n_bad == 0:
    print(f"{OK} 全部通过（{n_ok}/{len(results)}）—— 可以开始使用")
    print("\n下一步：")
    print("  cd proto && python -m cosmos_mem.cli_mcp --help")
    print("  或按 README「DSH 插件」一节安装到 DeepSeek Harness")
    code = 0
else:
    print(f"{BAD} 有 {n_bad} 项未通过（{n_ok}/{len(results)}）")
    print("\n需要修复：")
    for r in results:
        if not r["ok"]:
            print(f"  · {r['name']}: {r['detail']}")
            if r["fix"]:
                print(f"    {r['fix']}")
    code = 1

print("=" * 70)
sys.exit(code)
