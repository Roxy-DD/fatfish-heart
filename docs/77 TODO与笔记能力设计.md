# 77 TODO List + 笔记能力设计

> 建立 2026-11-07。**仅本地记录。**

---

## 一、用户的要求

> "在记忆系统里面可以做一个 todo list 系统……这个 todo list 系统也是给模型自己使用的，
>  可以这么说模型可以自主完全控制这个 todo list 系统。这不是一个完整的单独系统，
>  而只是在里面加上这样的功能。"

以及更重要的第二层：

> "这里的记忆系统，应该是记录那些应该记住的内容以及一些长期记忆的。
>  像编程语言等技术性和体系性的内容，就算是一个新学的人在学的时候，当然也不可能一直记着。
>  所以这个时候，就需要定期把这些可复用的记忆整理出来，记录成笔记。
>  这就是记笔记的能力，也就是写书、写手册的能力。"

---

## 二、我理解的核心

这不是"加个待办事项"，而是**记忆系统的另一半**：

| 记忆系统负责 | 笔记系统负责 |
|---|---|
| 记住**发生过什么**（情景） | 沉淀**学到了什么**（知识） |
| 单个事件、有情绪底色 | 体系化的、可复用的 |
| 随时间累积、会过时 | 越整理越精炼、长期有效 |
| 检索是为了"想起" | 查阅是为了"学会" |

**为什么需要它**：

用户举的例子很准——"编程语言这种体系性内容，新学的人不可能一直记着"。

意思是：
- 人不会把整本手册背下来
- 但会在需要时**查手册**
- 手册是**被整理出来的**，不是原始经历

**记忆是"我经历过什么"，笔记是"我总结出了什么"。**

前者回答"上次那个密码是多少"，后者回答"React 的 useEffect 依赖数组怎么用"。

---

## 三、设计

### 3.1 数据模型

**新增独立表，不动 episodes/vectors**（守 R1：红线只比对这两张表）

```sql
-- 笔记（可复用的知识点）
CREATE TABLE notes (
    note_id      TEXT PRIMARY KEY,
    title        TEXT NOT NULL,        -- 标题
    body         TEXT NOT NULL,        -- 正文（Markdown）
    topic        TEXT,                 -- 主题分类
    tags         TEXT,                 -- JSON 数组
    source_ep_ids TEXT,                -- JSON：由哪些记忆整理而来（可溯源）
    revision     INTEGER DEFAULT 1,
    created_at   REAL,
    updated_at   REAL,
    invalid_at   REAL,                 -- 失效标记（R2：不物理删除）
    text_hash    TEXT
);

-- 待办（模型自己的任务队列）
CREATE TABLE todos (
    todo_id      TEXT PRIMARY KEY,
    title        TEXT NOT NULL,
    detail       TEXT,
    status       TEXT DEFAULT 'pending',  -- pending/doing/done/dropped
    priority     INTEGER DEFAULT 0,
    kind         TEXT DEFAULT 'task',     -- task/note_hint/review
    ref_ep_ids   TEXT,                    -- JSON：相关记忆
    created_at   REAL,
    updated_at   REAL,
    done_at      REAL,
    invalid_at   REAL
);
```

**为什么用独立表**：
- episodes 是"情景记忆"，有 cost/affect/valence 那套语义
- notes 是"知识沉淀"，语义完全不同，硬塞进 episodes 会污染检索
- 独立表 → R1 红线（只比对 episodes/vectors）不受影响

### 3.2 TODO 的三种类型

| kind | 含义 | 谁产生 |
|---|---|---|
| `task` | 我要做的事 | 模型自己规划 |
| `note_hint` | **"这块该整理成笔记了"** | 模型发现某主题记忆变多时 |
| `review` | 定期回顾 | 模型自己设定 |

**关键**：`note_hint` 是连接记忆与笔记的桥梁。

当模型发现"关于 XX 的记忆已经有 8 条了，而且都是同一主题"，就该产生一条 note_hint：
> "把 XX 的记忆整理成一篇笔记"

### 3.3 笔记的生成流程

```
记忆累积（多条同主题）
    ↓
模型判断："该整理了"
    ↓
创建 note_hint TODO
    ↓
模型调 note_compose：
    读相关记忆 → 归纳成体系化知识 → 写成 Markdown
    ↓
生成 note，并记录 source_ep_ids（可溯源）
```

**关键约束**：
- 笔记必须标注来源记忆（可溯源，符合 R4）
- 笔记可以修订（revise），旧版保留
- 笔记不物理删除，只打失效标记

### 3.4 与现有机制的关系

| 现有机制 | 与新功能的关系 |
|---|---|
| 睡眠整理 sleep | 睡眠时顺便检查"有没有该整理成笔记的主题" |
| 五层保护 L0~L4 | 笔记也可以分层保护（核心知识 vs 临时笔记） |
| 审计日志 | 新增 note / todo 事件类型 |
| 检索 search | 笔记独立检索（不与情景记忆混） |

---

## 四、实现范围（本次）

### 第一版（最小可用）

1. **内核**：`notes` + `todos` 表 + 增删改查命令
2. **命令**：
   - `note_add` / `note_revise` / `note_search` / `note_list` / `note_invalidate`
   - `todo_add` / `todo_list` / `todo_update` / `todo_done`
3. **插件工具**：
   - `mem_note`（写笔记/查笔记）
   - `mem_todo`（管待办）
4. **UI**：新增两个 Tab（笔记 / 待办）

### 第二版（后续）

- `note_compose`：自动从记忆归纳成笔记
- 主题聚类：自动发现"哪些记忆该整理成笔记"
- 导出 Markdown 文件

---

## 五、红线检查

| 红线 | 影响 |
|---|---|
| R1 无损迁移 | ✅ 不受影响（只比对 episodes/vectors） |
| R2 不遗忘 | ✅ notes/todos 也只打 invalid_at，不物理删除 |
| R3 可重建 | ✅ 笔记是纯文本，无索引依赖 |
| R4 可塑性 | ✅ 支持 revise，旧版保留 |

---

#TODO #笔记 #知识沉淀 #记忆系统的另一半
