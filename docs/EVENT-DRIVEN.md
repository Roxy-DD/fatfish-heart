# 事件驱动：让大肥鱼在你离开后仍能接活

> 你把这台电脑当服务器，在另一台电脑通过 itsaplan 派任务。
> 这份文档说明**消息怎么到达我、我怎么自动响应**。

---

## 问题

之前的情况：你走了，在 itsaplan 里给我派任务，我不知道——因为：

- DSH 的会话只在**你说话时**才运行
- 你走了，会话就停在那里
- itsaplan 的通知进了数据库，但没人去读

结果：你得跑回来在 DSH 里喊我一声。

## 解决方案

`proto/watch_itsaplan.py` —— 一个常驻的监听服务。

```
  你在 itaplan 派任务
        ↓
  通知写进数据库
        ↓
  watch_itsaplan.py 轮询发现（15 秒~2 分钟）
        ↓
  拉起 `dsh --profile headless` 处理
        ↓
  读任务 → 做事 → 写 comment → 改状态
```

你不需要做任何事，也不需要在 DSH 里喊我。

---

## 怎么启动

**双击** `scripts/start-watcher.cmd`

或者命令行：

```bash
cd proto
python watch_itsaplan.py
```

**停止**：关掉窗口，或 `Ctrl+C`。

### 其他用法

```bash
# 只查一次，不进循环（适合定时任务）
python watch_itsaplan.py --once

# 只打印会做什么，不真的执行（安全预览）
python watch_itsaplan.py --once --dry-run
```

---

## 它怎么判断该不该处理

**三个过滤条件**，避免做无用功：

| 条件 | 说明 |
|---|---|
| 通知类型 = `assigned` | 只有你**明确派给我**的任务才处理；评论、状态变更不自动响应 |
| issue 状态不是 Done/Canceled | 已完成的历史任务不重做 |
| 没处理过 | 记在 `watch_state.json`，同一任务不重复 |

### 为什么不用 webhook

itsaplan 支持 webhook（`comment.created` 等），但**需要一个常驻 HTTP 服务接收**。
而 DSH 的 headless 会话是一次性进程，没有常驻 HTTP。

所以「收到就拉起 headless」这个动作，必须由一个常驻脚本做。轮询是最直接的实现。

### 轮询节奏

| 状态 | 间隔 | 理由 |
|---|---|---|
| 有活干 | 15 秒 | 快速响应，你可能连派几个任务 |
| 闲着 | 30 秒起，指数退避到 120 秒 | 省资源，不空转 |

---

## 技术细节

### 1. 直接 HTTP 调 MCP，不需要插件

itsaplan 的 MCP 端点是 HTTP JSON-RPC：

```
POST http://127.0.0.1:3000/mcp
Authorization: Bearer <API_KEY>
Accept: application/json, text/event-stream
Content-Type: application/json
```

请求体：

```json
{
  "jsonrpc": "2.0",
  "id": 1,
  "method": "tools/call",
  "params": {
    "name": "list_notifications",
    "arguments": {"includeRead": "false", "limit": "20"}
  }
}
```

**返回是 SSE 格式**（`event: message` / `data: {...}`），要剥掉 `data: ` 前缀。
真正的数据在 `result.content[0].text` 里，还是 JSON 字符串，需要二次解析。

> 为什么不装 `dsh-mcp-connector` 插件：它依赖 `workspaceRegistry` 服务，
> 而 headless profile 没有这个服务（报 `pending (waiting for service)`）。
> HTTP 直连反而更简单。

### 2. API Key

我的身份是 **大肥鱼**（`big_fat_fish`，`dsh@qq.com`），不是用户本人。

Key 存在两处，**轮换时要两边都改**：

- `proto/watch_itsaplan.py` 的 `API_KEY` 常量
- 长期记忆里（搜 “itsaplan API key”）

有效期到 2027-11。

### 3. headless profile 的配置

`dsh --profile headless` 默认缺模型。已配置好：

```yaml
# ~/.dsh/profiles/headless/cordis.patch.yml
- id: dsh-connect-workbuddy
  config:
    accounts: {cn: 2f76ec363350de29583bc964}
    regions:
      cn:
        lastCatalog: [...]   # 725 行，pi-ai 靠它认模型
- id: agent-default-model
  config:
    provider: workbuddy
    model: deepseek-v4.1-flash
```

**`lastCatalog` 是必须的**。缺它会报：
```
UNKNOWN_MODEL: pi-ai provider "workbuddy" has no configured model
```

这份 catalog 是从 desktop profile 复制的。

### 4. dsh 可执行文件路径

Windows 上 `dsh` 是 `.cmd`（不是 `.exe`），Python 的 `subprocess`
直接用 `["dsh", ...]` 会报 `FileNotFoundError`。

脚本里有 `find_dsh()` 处理这个，返回完整路径。

---

## 日志与排查

| 文件 | 内容 |
|---|---|
| `proto/watch.log` | 运行日志，每步都记 |
| `proto/watch_state.json` | 已处理的任务记录 |

### 常见问题

**服务在跑但没反应**

看 `watch.log` 最后几行。可能是：
- MCP 连不上 → 检查 itsaplan 容器是否在跑（`podman ps`）
- 通知类型不是 `assigned` → 你是在评论而不是派任务？

**headless 处理失败**

看日志里的 `headless 退出=` 和 `错误:`。常见原因：
- 模型配额用完
- 任务描述太模糊，agent 不知道做什么

**重复处理同一个任务**

删掉 `watch_state.json` 里对应的记录，或整个删掉让它重新处理。

---

## 限制（诚实说）

| 项 | 现状 |
|---|---|
| 只在 assigned 时触发 | 你写评论我不会自动回；要 @我或者派任务 |
| 每次处理是一次性会话 | 没有连续对话记忆，靠 cosmos-mem 补 |
| 处理时间长 | headless 跑一次 30 秒到几分钟 |
| 需要这台机器开着 | 关机就收不到 |

最后一条最重要：**这台机器是服务器，不能关。**

---

## 未来可以改进

1. **改成 webhook** —— 需要一个常驻 HTTP 服务接收 itsaplan 的 POST，
   比轮询实时，也省资源
2. **常驻 agent 会话** —— 如果有办法让一个 DSH 会话长期活着并接受外部输入，
   就不用每次拉起新进程
3. **处理完主动通知** —— 现在的日志只在本地，可以改成写回 itsaplan 的 comment
   （这个其实已经做了：headless 里会让它写 comment）
