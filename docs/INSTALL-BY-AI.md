# 让 AI 帮你安装

> **这份文档是写给 AI 看的。**
> 你可以把整份内容复制给你的 AI（Claude / ChatGPT / DeepSeek / Cursor / Cline
> 等任何能执行命令的工具），它会照着装好。

## 为什么单独写一份

大多数用户不会手动跑命令——他们会说「帮我装一下这个插件」。
但 AI 在没有明确指令时容易猜错步骤（比如猜 npm 包名、猜配置路径）。

这份文档的目标：**把 AI 需要知道的全部事实写清，让它一次做对。**

---

## 给 AI 的提示词（直接复制）

````text
请帮我安装「肥鱼之心」插件（dsh-fatfish-heart）。
这是一个给 DeepSeek Harness 用的情景记忆插件。

请按以下步骤做，每步完成后告诉我结果：

1. 确认环境
   - 检查 `dsh --version` 是否可用
   - 检查 `git --version` 是否可用
   - 检查 `python --version`（需要 3.10+）
   如果有任一缺失，先告诉我缺什么，不要继续。

2. 克隆仓库
   git clone https://github.com/Roxy-DD/fatfish-heart.git
   cd fatfish-heart

3. 安装 Python 依赖
   pip install -r requirements.txt

4. 下载嵌入模型（必需，约 100MB）
   pip install huggingface_hub
   python -c "from huggingface_hub import snapshot_download; snapshot_download('BAAI/bge-small-zh-v1.5', local_dir='models/bge-small-zh')"

5. 环境自检
   cd proto && python doctor.py
   必须看到「全部通过（11/11）」才继续。如果失败，把报错发给我。

6. 初始化记忆库
   python init_db.py

7. 安装插件到 DSH
   dsh plugin --profile desktop add <刚才 clone 下来的 fatfish-heart/dsh-plugin 的绝对路径>
   注意：是加到 dsh-plugin 目录，不是仓库根目录。

8. 重启 DSH
   插件需要重启应用才会加载。请提示用户手动退出并重开 DeepSeek Harness。

9. 验证
   重启后访问 http://127.0.0.1:<端口>/cosmos-mem/stats
   能看到 {"total": N} 就说明装好了。

如果任何一步失败，停下来告诉我具体报错，不要自己猜着改。
````

---

## AI 容易搞错的几个点（提前告诉你）

| 容易错的地方 | 正确做法 |
|---|---|
| **装哪个目录** | 是 `fatfish-heart/dsh-plugin`，不是仓库根目录 |
| **包名** | 本地安装用路径；市场安装用 `dsh-fatfish-heart` |
| **必须重启** | 装完不重启，插件不生效。DSH 的热重载不覆盖首次安装 |
| **模型要单独下** | 模型权重不进版本库（约 1.3GB），必须自己下载 |
| **Python 版本** | 需要 3.10+，3.9 会因为类型语法报错 |

---

## 如果你想让 AI 判断「值不值得装」

可以加上这段：

````text
安装前先帮我评估：
1. 读一下 https://github.com/Roxy-DD/fatfish-heart 的 README
2. 告诉我这个项目解决什么问题、有什么代价（磁盘占用、依赖、隐私）
3. 如果我认为不需要，就别装了，直接告诉我为什么不需要
````

---

## 通过插件市场安装（更简单，但需要 DSH 支持）

如果你的 DSH 版本支持插件市场，可以让 AI 这样做：

````text
1. 在 DSH 设置里添加市场索引：
   <市场索引 URL>
2. 搜索「肥鱼之心」并安装
3. 重启 DSH
````

> 市场索引 URL 目前需要手动配置。等我们上架到公共市场后，
> 这里会换成可直接添加的地址。

---

## 卸载

````text
dsh plugin --profile desktop remove dsh-fatfish-heart
````

记忆数据在 `proto/cosmos_mem/dsh_mem.db`，卸载插件**不会**删除它。
要彻底清除，手动删这个文件即可。

---

## 给 AI 的一句话总结（最简版）

如果你只想贴一句话：

````text
帮我装 https://github.com/Roxy-DD/fatfish-heart 这个 DSH 插件：
clone → pip install -r requirements.txt → 下载 BAAI/bge-small-zh-v1.5 模型
→ python proto/doctor.py 自检 → dsh plugin --profile desktop add <绝对路径>/dsh-plugin
→ 提示我重启 DSH。任何一步失败就停下来告诉我报错。
````
