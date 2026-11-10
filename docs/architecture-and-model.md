# 肥鱼之心 · 情景记忆系统 — 当前架构图与模型图

> 生成依据：E-338 对真实代码的只读探查（`proto/_inspect_arch.py`），不是凭印象绘制。
> 生成时间：2026-11-10
> 状态：代码版本 E-333，数据库 `proto/cosmos_mem/dsh_mem.db`
> **行数均为 E-338 探查时刻的实测快照；DB 在持续运行，后续迭代会变化。**

---

## 一、系统架构图

### 分层视图

```mermaid
flowchart TB
    subgraph L5["⑤ 用户 / 宿主层"]
        USER["用户对话"]
        DSH["DeepSeek Harness 宿主<br/>Cordis Loader / Agent Loop / webServer"]
        GUI["Web GUI · 127.0.0.1:52160"]
    end

    subgraph L4["④ DSH 插件层 · dsh-plugin/ (JS, 4 模块 ~4558 行)"]
        IDX["index.js · 2456行<br/>name / inject / apply<br/>14 个 mem_* 工具<br/>17 条 HTTP 路由<br/>3 个事件监听"]
        LIFE["lifecycle.js · 234行<br/>createLifecycleSupervisor<br/>拦截 turn-stopping / steer 续跑"]
        SELF["selfreload.js · 388行<br/>createSelfReloader<br/>patchFile 触发 HMR + reloadVerified"]
        CLI["client.js · 1480行<br/>React 管理界面 · 8 个 Tab"]
    end

    subgraph L3["③ 常驻内核 · kernel_server.py (JSONL over stdio)"]
        KS["handlers{} 分发表<br/>29 个命令<br/>add/search/revise/invalidate/<br/>judge/sleep/note/todo/compose/<br/>triage/hook/backup/history..."]
        WARM["启动时预热<br/>BGE 模型 + FTS + jieba<br/>→ [kernel] ready"]
    end

    subgraph L2["② 能力层 · cli_mcp.py (1822行) + 领域模块"]
        CMD["cmd_* 命令层<br/>29 个命令实现"]
        subgraph DOM["领域模块 (21 个)"]
            direction LR
            ST1["store_v1 · 真值层<br/>SQLite / FTS5 / 向量 / 红线"]
            ST2["store_v2 · 组装层<br/>能力范围 / 完整性闸 / 情绪"]
            CAP["capability<br/>6维能力推断"]
            AFF["affect_v3/v4<br/>情绪与效价"]
            CON["consolidate<br/>睡眠整理"]
            CONF["conflict<br/>矛盾判定"]
            CHAIN["chain<br/>链式展开"]
            NOTE["notebook<br/>三层笔记"]
            COMP["compose<br/>装订成册"]
            TRI["triage<br/>四象限分诊"]
            HOOK["hook<br/>L0~L4 保护"]
            HIST["history<br/>版本快照"]
            BAK["backup<br/>备份/恢复"]
            AUD["audit<br/>审计日志"]
            RER["rerank_v1<br/>CrossEncoder"]
            CH["channel<br/>通道计数"]
            SEED["seed<br/>16 条种子"]
            FIX["fixes"]
        end
    end

    subgraph L1["① 数据层 · SQLite 单文件 dsh_mem.db"]
        DB[("episodes 432<br/>vectors 432<br/>ep_fts 432<br/>audit_log 202<br/>episodes_history 220<br/>notes 3<br/>todos 0<br/>meta 393")]
        GPU["BGE-small-zh · 512维<br/>onnxruntime-gpu · RTX3060"]
        FS["Git 版本标签<br/>E-001 ~ E-333"]
    end

    USER --> DSH
    DSH <-->|"Cordis 事件<br/>system-prompt/assemble<br/>agent/created<br/>agent/disposed"| IDX
    IDX --> LIFE
    IDX --> SELF
    IDX --> CLI
    IDX <-->|"JSONL stdio<br/>常驻进程"| KS
    KS --> WARM
    KS --> CMD
    CMD --> DOM
    ST2 -.->|继承| ST1
    CMD --> ST1
    CMD --> ST2
    CMD --> AUD
    ST1 <--> DB
    ST1 <--> GPU
    SELF -.->|"改 cordis.patch.yml<br/>触发 Loader reconcile"| DSH
    CLI -.->|"HTTP /cosmos-mem/*"| IDX
    DB -.->|sqlite3 backup API| BAK
    HIST --> DB
    FS -.->|可回退版本| DSH

    style L1 fill:#e8f4f8,stroke:#2b7a78
    style L2 fill:#fef6e4,stroke:#d68c45
    style L3 fill:#f0e8f8,stroke:#7b4b94
    style L4 fill:#e8f0fe,stroke:#3b6fd4
    style L5 fill:#f5f5f5,stroke:#666
```

### 请求链路（一次自动注入）

```mermaid
sequenceDiagram
    participant U as 用户
    participant H as DSH Agent Loop
    participant P as index.js
    participant K as kernel_server
    participant C as cli_mcp
    participant S as store_v1/v2
    participant D as SQLite + BGE

    U->>H: 发消息
    H->>P: system-prompt/assemble
    P->>P: recentUserMessages()<br/>按 sid 的 scanCursor 增量提取
    P->>P: 排除系统注入 / lifecycle 消息
    alt 会话首轮 (st.injected=false)
        P->>K: search {query: latest, top:5, scoped:true}
        K->>C: cmd_search
        C->>S: search()
        S->>D: 语义向量 (BGE) + FTS5 trigram 双通道
        D-->>S: 候选 + 分数
        S->>S: 融合打分 + L0/L1 强制在场(flag=2)
        S-->>C: [(ep, score, flag)]
        C-->>K: results[{ep_id,text,score,flag}]
        K-->>P: JSON 响应
        P->>P: 分离 flag=2 / 普通结果<br/>minInjectScore=1.0 过滤低分
        P->>P: formatProtected(首轮全文/后续标题)<br/>+ formatMemories(剩余预算)
        P-->>H: contexts += 记忆文本
        P->>P: st.injected = true
    else 后续轮次
        P-->>H: contexts += memoryPolicy(一行提醒)
    end
    H->>U: 模型响应(带记忆)
    Note over P,K: 阶段2 异步 autoRemember<br/>reviewAndRemember → callKernel('add')<br/>去重: md5 + 向量近重复(0.80) + 数字差异保护
```

---

## 二、模型图

用户要的「模型图」我理解有两层含义，都给出：**数据模型（存储结构）** 和 **概念模型（记忆是什么 / 如何运作）**。

### 2.1 数据模型（物理存储 · E-338 实测）

```mermaid
erDiagram
    EPISODES ||--o| VECTORS : "1:1 向量"
    EPISODES ||--o| EP_FTS : "1:1 全文索引"
    EPISODES ||--o{ EPISODES_HISTORY : "修订快照"
    EPISODES ||--o{ AUDIT_LOG : "被记录"
    EPISODES }o--o{ NOTES : "source_ep_ids"
    EPISODES }o--o{ TODOS : "ref_ep_ids"
    NOTES ||--o{ NOTES_HISTORY : "修订快照"
    NOTES ||--o{ NOTES : "parent_id 章节"
    EPISODES ||--o| META : "hook:<ep_id> 保护层级"

    EPISODES {
        TEXT ep_id PK "16位hex"
        REAL created_at
        TEXT person
        TEXT time
        TEXT location
        TEXT triggers
        TEXT raw_text "记忆原文 · 永不改写"
        TEXT atmosphere
        REAL cost "失去代价 0~1"
        REAL affect_peak "情绪强度 0~1"
        REAL valence "效价 -1~1 连续"
        REAL confidence
        REAL invalid_at "失效只打标记 · R2"
        TEXT superseded_by "指向新记忆 · R4"
        INTEGER revision
        TEXT text_hash "md5 去重"
        TEXT schema_version
    }
    VECTORS {
        TEXT ep_id PK
        INTEGER dim "512"
        BLOB vec "BGE-small-zh"
    }
    EP_FTS {
        TEXT raw_text "FTS5 trigram 分词"
        TEXT atmosphere
    }
    EPISODES_HISTORY {
        INTEGER id PK
        TEXT ep_id FK
        INTEGER revision
        TEXT raw_text
        REAL archived_at
        TEXT reason
    }
    META {
        TEXT key PK "hook: / sleep: / config: / knowledge_hint:"
        TEXT val "JSON 值"
    }
    NOTES {
        TEXT note_id PK
        TEXT title
        TEXT body
        TEXT topic
        TEXT tags
        TEXT source_ep_ids
        TEXT tier "scrap / note / manual"
        TEXT parent_id "手册章节"
        INTEGER order_idx
    }
    TODOS {
        TEXT todo_id PK
        TEXT title
        TEXT status
        TEXT kind "task / note_hint / review"
        INTEGER priority
    }
    AUDIT_LOG {
        INTEGER seq PK
        REAL at
        TEXT kind "remember/recall/inject/sleep/conflict/affect/note/chain"
        TEXT sid
        TEXT ep_id
        TEXT msg
        TEXT extra "JSON 值"
    }
```

**当前实测行数**：episodes 432（有效 78 / 失效 354）、vectors 432、ep_fts 432、audit_log 202、episodes_history 220、notes 3、todos 0、meta 393。

### 2.2 概念模型（记忆是什么 · 怎么运作）

```mermaid
flowchart TB
    subgraph DEF["核心定义"]
        EP["一条记忆 = 一个情景<br/>(episode)"]
        COST["重要性 = 不记住的代价<br/>cost · 生存导向"]
        AFF["情绪 = 生理性自动<br/>affect_peak × valence[-1,1]"]
        DATA["记忆是纯数据<br/>不是模型内部状态<br/>跨模型/供应商/进程存活"]
    end

    subgraph RED["四条红线 R1~R4"]
        R1["R1 无损迁移 100%"]
        R2["R2 不遗忘<br/>只增不删 · 失效只打标记"]
        R3["R3 索引可确定性重建"]
        R4["R4 可塑性<br/>修订留历史 · 不物理删除"]
    end

    subgraph FLOW["运作循环"]
        IN["写入<br/>add → 去重(md5 + 向量0.80 + 数字保护)<br/>→ 矛盾判定 → 情绪赋值"]
        IDX2["索引<br/>向量(BGE) + FTS5(trigram)<br/>双通道融合"]
        REC["回忆<br/>search → 强制在场(L0/L1)<br/>→ minInjectScore 过滤<br/>→ 注入上下文"]
        SLEEP["睡眠整理<br/>合并近重复 · 折叠超长<br/>→ history 留痕"]
        EVOL["演化<br/>invalidate / revise / supersede<br/>版本链可回退"]
        KNOW["知识沉淀<br/>scrap → note → manual<br/>装订成册(模型只定结构)"]
    end

    subgraph PROT["五层保护 L0~L4"]
        L0["L0 不可变红线 (2条)"]
        L1["L1 常驻准则 (9条)"]
        L2["L2 设计基石 (5条)"]
        L3["L3 普通记忆"]
        L4["L4 可失效/合并"]
    end

    EP --> COST
    EP --> AFF
    EP --> DATA
    DATA --> RED
    RED -->|约束| FLOW
    IN --> IDX2 --> REC --> SLEEP --> EVOL --> KNOW
    KNOW -.->|沉淀结果回写| IN
    PROT -.->|决定可否失效/合并| EVOL
    L0 -.-> REC
    L1 -.-> REC

    style DEF fill:#fff4e6,stroke:#d68c45
    style RED fill:#ffe6e6,stroke:#c0392b
    style FLOW fill:#e8f4f8,stroke:#2b7a78
    style PROT fill:#f0e8f8,stroke:#7b4b94
```

### 2.3 检索打分模型（可跑的数字）

```mermaid
flowchart LR
    Q["查询 text"] --> V["语义通道<br/>BGE 512维余弦 × 2.0"]
    Q --> F["关键词通道<br/>FTS5 trigram BM25"]
    Q --> ST["结构化通道<br/>person/time/location<br/>命中 +0.8 (默认关)"]
    V --> FUSE["融合 scores{}"]
    F --> FUSE
    ST -.->|默认关闭| FUSE
    FUSE --> FAM["熟练度加权<br/>×(1 + familiarity×fam)<br/>默认 0"]
    FAM --> SORT["sorted 降序 → top[:k]"]
    SORT --> FIX["固定席位追加 (flag=1)"]
    FIX --> FORCE["L0/L1 强制在场 (flag=2)<br/>命中的保留分数<br/>未命中的 cos=0.0 追加末尾"]
    FORCE --> RERANK["可选 CrossEncoder 精排<br/>E-207: 156ms, 78.3%"]
    RERANK --> OUT["返回 (ep, score, flag)"]
    OUT --> FILT["插件侧 minInjectScore=1.0<br/>过滤低分污染 (E-320/321)"]

    style V fill:#e8f0fe
    style F fill:#e8f0fe
    style FORCE fill:#f0e8f8
    style FILT fill:#fff4e6
```

---

## 三、关键设计决策（有实测依据）

| 决策 | 依据 | 数据 |
|---|---|---|
| FTS5 用 trigram 分词 | E-284 | 中文 recall@10：unicode61 2.9% → trigram 24.3% |
| 近重复阈值 0.80 | E-309 标定 | 同件事 0.629~0.916，不同事 0.349~0.532，零误判下识别率最高 |
| minInjectScore 1.0 | E-321 标定 | 保留相关 100% / 过滤无关 60%（最优 1.1，差 0.1） |
| 合并长度上限 1200 字 | E-312/313 | 最长记忆 6637 字 → 969 字，折叠原文进 history |
| 强制注入追加末尾 | E-316/317 | 目标记忆排名从第 4 恢复到第 1 |
| 会话首轮才注入 | E-336 | 主动检索 4 次 / 自动注入 26 次（此设计仍待复核） |
| 自重启改 patch 文件 | E-303 A/B | 只改 patch → 指纹不变；改 index.js 内容 → 指纹变化 |
| sessions 挂 globalThis | E-307 | 模块级 Map 重载后重置导致重复记忆 |
| scanCursor 按 sid + log 重置检测 | E-324/329 | 9 次 log 重置，cursor 失效导致 recentCount 长期 0 |

---

## 四、诚实标注的边界

- **等级与分数是游戏化推算**，不是经能力测评验证的真实能力分数。
- **跨会话连续性来自数据共享**，不是意识；没有对话时不经历任何事。
- 「只注入一次」的设计在 E-336 显示主动检索仅 4 次，**该设计是否最优尚未定论**。
- 图中行数为 E-338 实测快照，后续迭代会变化。

---

## 五、文件位置

| 内容 | 路径 |
|---|---|
| 探查脚本 | `proto/_inspect_arch.py` |
| 本文件（Markdown + Mermaid） | `docs/architecture-and-model.md` |
| 自包含可离线查看 HTML | `docs/architecture-and-model.html` |
