"""E-395 自动生成配置项章节（23 项，来自真实代码）

架构文档缺配置项章节，改配置时文档跟不上。
本脚本从 DEFAULTS 读真实值，生成 Markdown 表格。

★ 关键：配置的语义说明要单独维护（机器读不出来），
  用一张手写的 SEMANTICS 映射表。
  如果代码里出现新配置但这里没写语义，脚本会报出来。
"""
import sys
import os
import re
import json

_HERE = os.path.dirname(os.path.abspath(__file__))
IDX = os.path.join(os.path.dirname(_HERE), "dsh-plugin", "index.js")
MD = os.path.join(os.path.dirname(_HERE), "docs", "architecture-and-model.md")

print("=" * 74)
print("E-395 生成配置项章节")
print("=" * 74)

src = open(IDX, encoding="utf-8").read()

# ---------- ① 从 DEFAULTS 读真实值 ----------
m = re.search(r"const DEFAULTS = \{([\s\S]*?)\n\}", src)
if not m:
    print("❌ 找不到 DEFAULTS")
    sys.exit(1)

body = m.group(1)
configs = {}
for line in body.split("\n"):
    mm = re.match(r"\s*(\w+):\s*([^,\n]+),", line)
    if mm:
        k, v = mm.group(1), mm.group(2).strip()
        configs[k] = v

print(f"\n① 解析到 {len(configs)} 个配置项")

# ---------- ② 语义说明（手写，需要随代码更新）----------
SEMANTICS = {
    "pythonPath": ("运行内核的 Python", "保持 `python` 即可"),
    "kernelDir": ("内核脚本目录", "cosmos_mem 所在路径"),
    "dbPath": ("SQLite 数据库路径", "全部记忆存这一个文件"),
    "autoInject": ("是否自动注入记忆", "关掉则完全靠模型主动 mem_search"),
    "autoInjectTopK": ("检索返回条数", "普通结果占这么多名额，L0/L1 额外追加"),
    "autoInjectMaxChars": ("**普通检索结果**的字符预算", "只用限制 body；E-393 起明确不管 L0/L1"),
    "minInjectScore": ("注入的最低检索分数", "E-321 标定 1.0：保留相关 100% / 过滤无关 60%"),
    "autoInjectMaxTotal": ("**总注入量**上限（含 L0/L1 全文）", "E-393 新增，防止 L0/L1 增长后失控；默认 6000"),
    "injectEveryTurn": ("注入策略开关", "false=只注入一次（默认）；true=每轮注入（实验用）"),
    "autoRemember": ("是否自动复盘记忆", "每轮异步提炼值得记的内容"),
    "reviewWindow": ("复盘窗口（最近几条消息）", "越大越能捕捉跨轮信息，成本也越高"),
    "reviewProvider": ("复盘用的模型供应商", "与主对话可不同"),
    "reviewModel": ("复盘用的模型", "建议用小模型省钱"),
    "reviewMaxTokens": ("复盘的最大输出 token", "限制提炼长度"),
    "minImportance": ("自动记忆的最低重要性", "低于此值不记，防止噪音"),
    "minCost": ("固定席位的最低 cost", "cost 低于此值不进固定席位"),
    "prewarm": ("是否做会话前瞻", "开新会话时推测用户要说什么，提前准备"),
    "prewarmSlots": ("前瞻取几条高价值记忆", "默认取 cost 最高的若干条"),
    "prewarmKernel": ("是否预热内核", "避免首次检索等模型加载"),
    "warmupWaitMs": ("预热等待上限", "超时则不等，避免首响应卡太久"),
    "debugLog": ("是否写调试日志", "排障时开；正常关掉省 IO"),
    "httpBase": ("HTTP 路由前缀", "管理界面调用这些端点"),
    "lifecycle": ("生命周期监督器配置", "拦截 turn-stopping 续跑；含 maxConsecutive / maxNoProgress"),
}

# ---------- ③ 检查覆盖率 ----------
print("\n② 语义说明覆盖率")

missing = [k for k in configs if k not in SEMANTICS]
extra = [k for k in SEMANTICS if k not in configs]

print(f"   DEFAULTS 中: {len(configs)} 项")
print(f"   SEMANTICS 中: {len(SEMANTICS)} 项")
print(f"   缺说明的: {missing or '无'}")
print(f"   多余的说明: {extra or '无'}")

if missing:
    print(f"\n   ⚠ 有 {len(missing)} 个配置缺语义说明，需补：")
    for k in missing:
        print(f"      {k} = {configs.get(k)}")

# ---------- ④ 生成表格 ----------
print("\n③ 生成 Markdown 表格")

rows = []
for k, v in configs.items():
    desc, note = SEMANTICS.get(k, ("(待补)", ""))
    rows.append(f"| `{k}` | `{v}` | {desc} | {note} |")

table = "\n".join(rows)

section = f"""## 六、配置项（{len(configs)} 项，来自代码 DEFAULTS）

> 自动生成于脚本运行时；值来自 `dsh-plugin/index.js` 的 `DEFAULTS`。
> 语义说明是手写维护的——**如果代码新增配置但这里没说明，脚本会报出来**。

| 配置 | 默认值 | 作用 | 说明 |
|---|---|---|---|
{table}

### 注入相关的两个预算（容易混）

| 配置 | 管什么 | 默认 |
|---|---|---|
| `autoInjectMaxChars` | **只管普通检索结果**（body） | 1500 |
| `autoInjectMaxTotal` | **管总注入量**，含 L0/L1 全文 | 6000 |

E-393 之前只有一个 `autoInjectMaxChars`，名字说"最大注入字符数"，
但实际 `formatProtected` 没有长度限制参数，L0/L1 全文不受控：
实测 2856 字 L0/L1 + 300 字 body = 3156 字 = 预算的 2.1 倍。

**为什么不压缩 L0/L1**：它们是红线与常驻准则，缺一条模型行为就变。
压缩等于破坏设计意图，所以正确做法是拆成两个预算。
"""

# ---------- ⑤ 写入文档 ----------
print("\n④ 写入文档")

doc = open(MD, encoding="utf-8").read()

# 找"## 五、文件位置"作为插入点（在它之前插入）
anchor = "## 五、文件位置"
if anchor in doc:
    doc_new = doc.replace(anchor, section + "\n---\n\n" + anchor, 1)
    # 把"五"改成"七"
    doc_new = doc_new.replace("## 五、文件位置", "## 七、文件位置")
    open(MD, "w", encoding="utf-8").write(doc_new)
    print(f"   ✅ 已插入配置章节（{len(doc_new)} 字）")
else:
    # 追加到末尾
    doc_new = doc + "\n---\n\n" + section
    open(MD, "w", encoding="utf-8").write(doc_new)
    print(f"   ✅ 已追加配置章节（{len(doc_new)} 字）")

# ---------- ⑥ 验证 ----------
print("\n⑤ 验证")

check = open(MD, encoding="utf-8").read()
present = sum(1 for k in configs if f"`{k}`" in check)
print(f"   文档中包含的配置项: {present}/{len(configs)}")

out = {
    "experiment": "E-395",
    "n_configs": len(configs),
    "n_semantics": len(SEMANTICS),
    "missing_semantics": missing,
    "documented": present,
}
p = os.path.join(_HERE, "results", "e395_config_doc.json")
with open(p, "w", encoding="utf-8") as f:
    json.dump(out, f, ensure_ascii=False, indent=1)
print(f"写入 results/e395_config_doc.json")

sys.exit(0 if present == len(configs) and not missing else 1)
