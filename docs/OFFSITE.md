# 异地副本

> 本地备份防的是「文件被删」，异地副本防的是「机器没了」。

## 为什么需要

`proto/cosmos_mem/dsh_mem.db` 是你的全部记忆（对话、偏好、情绪、人际关系）。
本地自动备份每 20 条写入触发一次，保留最近 10 份——**但都在同一台机器上**。

磁盘坏了、笔记本丢了、房子烧了，本地备份全部一起没。

异地副本把加密后的快照推到 GitHub 私有仓库，机器没了记忆还在。

---

## 安全性

记忆里有私人内容，所以做了三层保护：

### 1. 目标仓库硬编码

`proto/offsite.py` 顶部：

```python
ALLOWED_REPO = "Roxy-DD/fatfish-heart-memory"
ALLOWED_URL  = "https://github.com/Roxy-DD/fatfish-heart-memory.git"
```

**不是环境变量，不是配置文件。** 想换仓库必须改代码。
这是防止「手滑推到公开仓库」的最后一道门。

### 2. 推送前实时校验仓库是 private

```python
gh api repos/Roxy-DD/fatfish-heart-memory --jq .private
# 必须返回 true，否则拒绝推送
```

不是相信配置里的值，是每次真的问一遍 GitHub。

### 3. 推送前校验本地库健康

```python
PRAGMA integrity_check   # 必须 ok
SELECT COUNT(*) FROM episodes  # 必须 > 0
```

**这一条比看起来重要。** 如果本地库已经损坏（截断、页损坏），
推上去就是把坏数据固化到远端 —— 而你可能几个月后才发现，
那时所有历史备份都是坏的，异地副本等于完全失效。

宁可这次不推，也不推一份坏的。

---

## 用法

```bash
cd proto

# 看状态（会检查仓库是不是 private）
python offsite.py status

# 推一次
python offsite.py push

# 校验远端那份能不能真恢复
python offsite.py verify
```

### `status` 看到什么

```
目标仓库: Roxy-DD/fatfish-heart-memory（硬编码，不可改）
private 校验: ✅ ok

本地数据库: 8192 KB
指纹: 7abc124629daa70c

上次同步: 2026-11-17 20:05（0.1 小时前）
上次文件: memory_20261117_200527.db
```

### `verify` 做的是真校验

不是「文件存在」，而是：

1. 通过 `download_url` 下载原始字节
2. 算 sha256，跟清单里记录的一致
3. **真的用 sqlite 打开它**，数一下 `episodes` 和 `vectors`

实测输出：

```
远端备份数: 1
最新: memory_20261117_200527.db
大小: 8196 KB
指纹: 72fc18a253a3025c

下载: 8196 KB（远端声明 8196 KB）
指纹校验: ✅ 一致
可恢复性: ✅ episodes=595 vectors=591
```

最后一行是关键：**证明远端那份能恢复出一个可用的记忆库**，
而不是只有文件传上去了。

---

## 什么时候跑

**目前不自动定时。** 手动跑，或者你自己挂 cron。

建议节奏：**每周一次**。

理由：全量快照 8MB，年增长约 139MB。
按周同步 = 30MB/月，成本可接受，且最多丢一周的记忆。
按天同步省不了什么，反而让仓库膨胀更快。

### 为什么不自动

自动推送如果配置错了（比如仓库权限变了、glibc 更新导致 sqlite 不兼容），
可能把错误的数据固化到远端，而你要几个月后才发现。

先手动跑几周，确认流程稳定，再考虑自动化。

如果要自动化，建议加一道检查：推送前先 `verify` 上一次的备份能不能打开。

---

## 为什么不接入 mem_sleep 自动推送

这里有个看起来"顺手"的事要刻意不做：`mem_sleep` 已经会触发本地备份，
再顺手加一个异地推送似乎很自然。**但没有加**，理由：

1. **涉及外网 + 私人数据**，不该隐式触发。
   用户说"整理一下记忆"时，不应该同时也把记忆上传到微软的服务器。

2. **push 要 clone 整个仓库**（现在 8MB+，以后更大），
   耗时数十秒，会拖慢睡眠流程。

3. **失败会污染睡眠**。睡眠是记忆整理的主流程，
   不该因为网络问题失败而让整个整理报错。

### 那怎么自动化

如果你要定时，用系统 cron / 计划任务调 `offsite.py push`，
让它在记忆整理之外独立运行：

```bash
# Linux/macOS crontab：每周一 03:00
0 3 * * 1 cd /path/to/fatfish-heart/proto && python offsite.py push >> /tmp/offsite.log 2>&1

# Windows 计划任务同理
```

这样即使推送失败，也不影响记忆整理。

---

## 技术细节

### 为什么用 SQLite backup API 而不是 copy

数据库跑在 WAL 模式。直接 `copy` 文件会拿到一个**没有合并 WAL 的残缺副本**
（E-498 故障矩阵的 F2 项实测过：裸拷贝主库只得 0 条记忆）。

`sqlite3.Connection.backup()` 会做一致性快照。

### 为什么下载不用 Contents API

**踩过的坑**（2026-11-17）：

GitHub Contents API 对 **>1MB 的文件不返回 base64**，
`.content` 字段是空字符串，而且**不报错**。

于是 8MB 的 db 下载下来是个空文件 → 指纹不符 → sqlite 报 `no such table`。

正确做法：

```bash
# 拿下载地址
gh api repos/OWNER/REPO/contents/FILE --jq .download_url
# 然后 curl 下原始字节
curl -sL -o FILE "<download_url>"
```

### 分支必须显式指定（踩过两次的坑）

GitHub 的 contents API **不指定 ref 时读默认分支**。
这一点看起来无害，但会造成一个极难发现的 bug：

```
push 报「✅ 已推送」    ← 成功
verify 报「✅ 指纹一致」 ← 也成功
但你看的是**上一次**的备份
```

实测踩了两次（2026-11-18）：
1. 第一次：读 API 不指定 ref → 读到默认分支；而数据推到了另一个分支
2. 第二次：加了 `BRANCH="master"` 常量修「读」，却漏改「写」——
   push 目标仍是硬编码的 `HEAD:main`

两次的症状完全相同：**push 成功、verify 通过、但看到的不是这次的数据**。

#### 现在怎么做的

不再硬编码，改成运行时解析：

```python
def branch():
    # 问 GitHub 这个仓库的 default_branch，拿不到才回退 master
    ...
```

6 处引用全部走这一个函数：5 处 `contents API` 的 `?ref=` + 1 处 `push` 目标。

**读写用同一个函数，且该函数跟随仓库真实状态。**

#### 教训

这类 bug 的共同特征：**所有正常指标都通过**
（退出码 0、日志无错、返回值正确），但实际做的是错的事。

发现它只能靠**跨分支对照实际数据**——直接查每个分支上有什么，
而不是看脚本报告。所以验证时要问自己：

> 我验证的是**对象**正确，还是只验证了**流程**跑通？

---

### 远端仓库结构

```
fatfish-heart-memory/
├── MANIFEST.json              # 备份清单（名称/大小/sha256/时间）
└── memory_YYYYMMDD_HHMMSS.db  # 每次一份全量快照
```

清单保留最近 20 条记录，但**旧的 .db 文件不会自动删**。
要清理的话手动删，或者以后加 prune。

---

## 恢复步骤（机器没了怎么救回来）

```bash
# 1. 新机器上装好项目
git clone https://github.com/Roxy-DD/fatfish-heart.git
cd fatfish-heart

# 2. 从私有仓库拉回最新记忆
gh auth login   # 需要这个账号有 fatfish-heart-memory 的权限
gh api repos/Roxy-DD/fatfish-heart-memory --jq '.private'  # 应为 true

# 3. 下载最新的备份（用 download_url，别用 contents）
URL=$(gh api repos/Roxy-DD/fatfish-heart-memory/contents/MANIFEST.json --jq .content | base64 -d | python -c "import json,sys;print(json.load(sys.stdin)['backups'][-1]['name'])")
DL=$(gh api "repos/Roxy-DD/fatfish-heart-memory/contents/$URL" --jq .download_url)
curl -sL -o proto/cosmos_mem/dsh_mem.db "$DL"

# 4. 验证
cd proto && python doctor.py     # 应 11/11 通过
python offsite.py verify         # 指纹应一致
```

---

## 演练过：机器没了能不能回来

这是异地副本唯一的意义，所以真的演练过，不是"应该能行"。

测试模拟一台**全新机器**：本地什么都没有，只能从 GitHub 恢复。
全程在临时目录，生产库不动。

验证三层，缺一层都不算真恢复：

| 层次 | 验证内容 | 实测结果 |
|---|---|---|
| ① 能下载 | 指纹与清单一致 | ✅ |
| ② 能打开 | integrity_check + 条数 | ✅ episodes=603 |
| ③ **能真正使用** | 检索有命中 + 能写入 | ✅ 命中 13 条、603→604 |

第三层是关键。很多验证只做到第二层，
但**一个能打开却检索不出东西的库，等于没恢复**。

对应测试：`proto/run_e525_disaster_drill.py`（已纳入回归）

---

## 诚实边界

| 项 | 现状 |
|---|---|
| 自动定时 | ❌ 未实现，需手动或自挂 cron |
| 增量同步 | ❌ 每次全量 8MB |
| 远端清理 | ❌ 旧备份不会自动删 |
| 加密 | ⚠ 只靠 GitHub 私有仓库权限，db 本身不加密 |
| 恢复演练 | ✅ 已实测（下载 → 指纹一致 → 能打开数数据） |

最后一条「加密」值得说清楚：如果有人拿到你 GitHub 账号的访问权，
就能下载这些记忆的明文。这是选择方案 A（放 GitHub）时已知的取舍。
