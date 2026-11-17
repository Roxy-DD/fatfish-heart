# 安装指南

> 面向**没有 DSH 使用经验**的人。如果你已经装好了 DSH，可直接跳到[第二步](#第二步安装插件)。

---

## 前置：你需要什么

| 需要 | 为什么 | 怎么检查 |
|---|---|---|
| **DeepSeek Harness** | 插件宿主 | `dsh --version` |
| **Python 3.10+** | 记忆内核是 Python | `python --version` |
| **约 1.5GB 磁盘** | 模型权重 | —— |

> 没有 DSH 也能用核心记忆系统（纯 Python + SQLite），
> 只是没有「自动注入上下文」这部分。见文末。

---

## 第一步：准备项目

```bash
git clone https://github.com/Roxy-DD/fatfish-heart.git
cd fatfish-heart
```

### 装 Python 依赖

```bash
pip install -r requirements.txt
```

> 如果你没有 GPU，把 `requirements.txt` 里的
> `onnxruntime-gpu` 改成 `onnxruntime`，速度慢些但能跑。

### 下载模型（必需）

```bash
pip install huggingface_hub
huggingface-cli download BAAI/bge-small-zh-v1.5 --local-dir models/bge-small-zh
```

### 验证

```bash
python proto/doctor.py
```

应该看到：

```
✅ 全部通过（11/11）—— 可以开始使用
```

**没通过就先别继续。** 缺什么它会让告诉你怎么装。

### 初始化记忆库

```bash
python proto/init_db.py
```

建 14 张表 + 灌 16 条种子记忆。输出：

```
✅ 初始化完成，可以跑实验了
```

---

## 第二步：安装插件

### 2.1 确认 DSH 可用

```bash
dsh --version
```

### 2.2 找到你的 profile 名

```bash
dsh profile list
```

常见的是 `desktop` 或 `web`。下面用 `<profile>` 指代。

### 2.3 安装

```bash
dsh plugin --profile <profile> add <项目绝对路径>/dsh-plugin
```

例如 Windows：

```powershell
dsh plugin --profile desktop add C:\Users\你\fatfish-heart\dsh-plugin
```

### 2.4 重启 DSH

插件需要重启宿主才能加载。

### 2.5 验证

打开 DSH 的 Web 界面，应该看到 **fatfish-heart** 的管理面板（6 个 Tab）。

或者问模型：

```
你还记得什么？
```

如果模型能调用 `mem_search`，说明插件活着。

---

## 配置说明

**通常不需要改任何配置。** 路径是自动推导的。

插件在首次加载时会从自己的位置推算出：

- `kernelDir` → `<项目根>/proto/cosmos_mem`
- `dbPath` → `<项目根>/proto/cosmos_mem/dsh_mem.db`

### 什么时候需要改

| 情况 | 改什么 |
|---|---|
| Python 不在 PATH 里 | `pythonPath` 改成绝对路径 |
| 插件被单独复制到别处 | `kernelDir` / `dbPath` 手动指定 |
| 想换记忆库位置 | `dbPath` |

配置文件位置：

```
~/.dsh/profiles/<profile>/cordis.patch.yml
```

---

## 只用核心记忆系统（不用 DSH）

不需要 DSH，直接用 Python 调：

```python
import sys
sys.path.insert(0, 'proto/cosmos_mem')
from store_v2 import MemoryStore

st = MemoryStore('proto/cosmos_mem/dsh_mem.db')
st.add('用户喜欢简洁的回答，不喜欢冗长解释')

r = st.search('他喜欢什么样的回答', top=3)
for ep, score, *_ in r:
    print(score, ep.raw_text)
```

也可以走 HTTP 内核（插件用的就是这条）：

```bash
cd proto
python cosmos_mem/kernel_server.py cosmos_mem/dsh_mem.db
```

---

## 常见问题

### `doctor.py` 报模型不存在

模型没下载或路径不对。检查：

```bash
ls models/bge-small-zh/
# 应该有 config.json / model.safetensors / tokenizer.json
```

### `init_db.py` 报「库已存在」

这是保护机制，防止覆盖你的真实记忆。
如果确实要重建：

```bash
python proto/init_db.py --force   # 会先备份
```

### 插件面板显示空白

1. 确认重启过 DSH
2. 看日志：`~/.dsh/profiles/<profile>/` 下找 `*.log`
3. 问模型能否调用 `mem_stats`

### 插件报内核启动失败

最常见原因：`pythonPath` 指向的 Python 没装依赖。

```bash
<你的python路径> -c "import numpy, jieba, transformers"
```

缺哪个装哪个。

---

## 卸载

```bash
dsh plugin --profile <profile> remove @local/dsh-fatfish-heart
```

记忆数据在 `proto/cosmos_mem/dsh_mem.db`，删不删由你定。
