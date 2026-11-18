# 真实测试与比赛规划（FISH-26 · 完整版）

> 调研日期：2026-11-18
> 数据来源：官网 + GitHub + HuggingFace + OpenAPI（均实际抓取核实）
> 状态：AML / 自助基准已核实；资助平台待补

---

## 0. 一句话结论

**能自助跑的 benchmark 现在就能跑；AML 比赛 Cycle 2 已截止，要等 Cycle 3。**
**R1/R2/R3（无损迁移/不遗忘/容灾重建）没有任何现成基准覆盖——这是我们的原创机会。**

---

## 1. AML（Agent Memory Leaderboard）—— 最对口，但要等

### 1.1 真实状态（已核实）

| 项 | 事实 |
|---|---|
| 官网 | https://agentmemoryleaderboard.ai/ |
| GitHub | https://github.com/AML-memory/agent-memory-leaderboard |
| OpenAPI | https://agentmemoryleaderboard.ai/api/openapi.json |
| Cycle 2 报名 | **2026-10-31 已截止** |
| Cycle 2 评测结束 | 2026-11-04 |
| 榜单发布 | 预计 2026-11-19（明天）|
| Cycle 1 结果 | 2026-08-12 已发布 |

**「现在就能提交 AML」不成立**——Cycle 2 已进入评测阶段，不能新报名。

### 1.2 关键修正：开源组也必须自己部署 API

官方文档明确：

> Open-source methods and commercial products both use participant-hosted Add/Search APIs. **AML does not deploy repository-only submissions.**

开源组不再是「提交 GitHub 仓库就行」，必须自己部署**公网可访问**的 Add/Search API。
文档明确禁止回环/私有/链路本地地址。

**这是最大技术障碍**：本机 Windows 的 SQLite 要暴露到公网 HTTPS，还要承受并发。

### 1.3 协议细节（来自官方 OpenAPI，已核实）

**认证**（参赛方端点侧）：
- 表单字段 `api_auth_scheme`，枚举：`none | token | bearer | x-api-key`（默认 token）
- Key 字段 `memory_api_key`
- 平台侧调用用 `X-Leaderboard-Api-Key`
- header 字面前缀需邮件确认（contactus@agentmemoryleaderboard.ai）

**评测任务字段**（`EvalJobCreate`）：
- mode: `full | light | smoke | scriptmem`（light/scriptmem 未公开解释）
- max_add_concurrency: 1–64
- search_concurrency: 1–256
- top_k: 1–1000
- dataset_ids（必填数组）

**注册表冻结项**（`RegistryDefinition`）：
phase / dataset_manifest_hash / evaluator_code_hash / dependencies_hash /
answer+judge 模型 snapshot / prompt_hashes / aggregation_policy / top_k /
score_scale / contract_version / benchmark_contract

——这是「统一评测边界/版本固定」的硬证据，正好对应我们的 R3 可重建理念。

### 1.4 其他已核实约束

- 免费，无团队人数要求
- 三赛道：Textual / Coding / Multimodal Memory
- 组别：开源方法榜 / 商业产品榜
- 流程：提交接入 → 审核发 Key → 公开 Smoke → Full → 复核 → 上榜
- 正式评测 Top K = 100（默认，可配到 1000）
- 开源组 Add 阶段限定 `gpt-4o-mini`
- 每 Key 每赛道最多 2 次 Full，第 2 次要等首次完成 30 天
- 提交表有 `public_endpoints_confirmed` 布尔（佐证公网是硬性要求）
- Cycle 2 主办：CSIG，南大/浙大/Datawhale 协办，奖金 22,000 美元（仅开源组）

### 1.5 我们要做的（Cycle 3 前）

1. 实现 Add / Search API 符合协议
2. **解决公网 HTTPS 部署**（最大障碍，需单独决策）
3. GitHub 仓库材料（已有）
4. 技术报告突出确定性检索/容灾/不漂移
5. 等 Cycle 3 开放

---

## 2. 自助 Benchmark（现在就能跑，无门槛）

### 2.1 总览

| 基准 | 测什么 | 规模/语言 | 主指标 | 本地跑 | 公开榜单 | 适配度 |
|---|---|---|---|---|---|---|
| **LMEB** | 纯 embedding 长程检索（4类记忆、193任务） | 1M–10M 行，英文 | **NDCG@10** | ✅ | ✅ MTEB 榜单 | 中（测 R3 重建一致性）|
| **LoCoMo** | 超长对话 QA/摘要，5类问题 | 10对话/1986QA，英文 | F1/judge/Recall | ✅ | ❌ | 中高（你已跑 Recall@10=0.5802）|
| **LongMemEval** | 5能力：抽取/多会话/知识更新/时序/拒答 | 500题，英文 | judge+Recall@K/NDCG@K | ✅ | V2 有提交 | 中高（对应 R4）|
| **MemoryAgentBench** | 检索/测试时学习/长程/**冲突消解** | 2071题，英文 | Exact Match/Recall@5/F1 | ✅ | ❌ | **最高**（冲突消解=R4）|
| **LongMemEval-V2** | web/enterprise agent 轨迹 | 451题，最高115M tokens | **LAFS（准确率+延迟）** | ✅ | ✅ 官方表单 | 高（唯一测延迟）|
| MemBench | 事实+反思记忆，参与/观察双场景 | 中文为主 | Accuracy/Recall/Capacity/时序效率 | ⚠️ | ❌ | 中 |
| MemoryArena | 多会话 agentic 任务记忆 | 5配置 | Success/Progress | ✅ preview | 社区 | 中 |
| THUIR/MemoryBench | 记忆+持续学习 | 28数据集，多语言 | 多任务 | ✅ | ❌ | 低-中 |

### 2.2 逐个详情

#### LMEB（MTEB 长时序记忆基准）★ 纯检索

- GitHub: https://github.com/KaLM-Embedding/LMEB
- HF 数据: https://huggingface.co/datasets/KaLM-Embedding/LMEB
- 论文: https://arxiv.org/abs/2603.12572
- 榜单: https://mteb-leaderboard.hf.space/benchmark/LMEB

测 4 类记忆：情景（时间/实体/空间线索）、对话（多轮偏好）、语义（稳定事实）、程序性（技能序列）。
22 数据集、193 zero-shot 任务、全英文。主指标 **NDCG@10**。

本地跑：
```bash
pip install mteb
huggingface-cli download --repo-type dataset KaLM-Embedding/LMEB --local-dir ./eval_data
mteb run -m MODEL -t TASK
mteb submit-results --create-pr    # 需 mteb[github] + gh auth login
```
提交 PR 后约 1 天上榜（每日 8AM UTC 工作流）。

**适配判断**：✅ 强烈建议。纯检索不依赖生成模型，我们的 SQLite+FTS5+向量可直接接入。
**正好量化 R3**：重建前后各跑一遍，逐条比较 NDCG@10 一致。
⚠️ 测不出 R1 迁移/R2 不遗忘/容灾。

#### LoCoMo（已在跑）

- GitHub: https://github.com/snap-research/locomo
- 论文: ACL 2024 https://aclanthology.org/2024.acl-long.747/

10 对话 / 1986 QA / ~16K tokens。5 类 QA（单跳/多跳/时序/常识/对抗拒答）。
指标：归一化 F1 / LLM-judge / 检索 Recall。无官方榜单（仅第三方汇总）。

关键边界（我们已实测）：
- 多跳题答案仅 15.8% 在证据原文 → 是推理不是检索
- 单跳失败 88% 是语义距离
- 我们的 Recall@10=0.5802，与厂商答题 F1 **不可直接比**

#### LongMemEval（ICLR 2025）

- GitHub: https://github.com/xiaowu0162/LongMemEval
- HF: https://huggingface.co/datasets/xiaowu0162/longmemeval-cleaned
- 论文: https://arxiv.org/abs/2410.10813

500 题。S 版 ~115K tokens，M 版 ~1.5M。5 能力含 **knowledge-update**（事实变更后答新值）和 **abstention**（拒答 30 题）。
检索指标 Recall@k/NDCG@k，QA 用 LLM-judge（gpt-4o-2024-08-06，与人类一致率 >97%）。

**适配判断**：knowledge-update + abstention 直接测 R4「修订/失效标记/旧值保留可溯源」。
⚠️ 我们 docs/61 说「HF 404」是旧状态，现在 cleaned 版可下载（已核实）。

#### MemoryAgentBench（ICLR 2026）★ 冲突消解最贴 R4

- GitHub: https://github.com/HUST-AI-HYZ/MemoryAgentBench
- 论文: https://arxiv.org/abs/2507.05257

2071 题，103K–1.44M tokens。4 能力：准确检索 / 测试时学习 / 长程理解 / **冲突消解（新记忆覆盖旧记忆）**。
新增 EventQA、FactConsolidation（选择性遗忘）数据集。
指标：substring_exact_match / Recall@5 / LLM-judge / F1。

**适配判断：最高**。Conflict Resolution 直接测「知识更新时不漂移、旧值不丢可溯源」= R4 核心。

#### LongMemEval-V2（唯一带延迟维度）

- GitHub: https://github.com/xiaowu0162/LongMemEval-V2
- **提交表单**: https://forms.gle/rxUpiuRKDERqpqSi9 （从官方 raw README 提取；GitHub 渲染版吃掉了链接）

451 题，最高 115M tokens，web+enterprise 双域，含多模态截图。
指标 **LAFS gain（准确率 vs 延迟前沿）** —— 唯一把延迟纳入打分的基准。

提交流程（已核实）：
```bash
# Step 1: 每个延迟工作点
python build_submission_step_1_single_operating_point.py \
  <web_dir> <enterprise_dir> <name> <fast|balanced|accurate> <small|medium>
# Step 2: 打包
python build_submission_step_2_build_package.py \
  <name> SYSTEM_DESCRIPTION.md <code_file> <op_dirs...>
```
两层强制校验：reader=qwen3.5-9b、judge=gpt-5.2、同 method/同 tier/同 question ids。
打分：overall_full_set / gotchas / static / dynamic / procedure accuracy +
**memory_query_avg_seconds**，最终分 = 对固定参考前沿的 LAFS gain。
**明确禁止用 GitHub issue 提交**。

**适配判断**：唯一纳入延迟的基准，我们的确定性检索+容灾恢复时间是独特卖点，值得做。

#### 其他

- **MemBench (Tan et al. 2025, arXiv 2506.21605)**（人大+华为，https://github.com/import-myself/Membench）：唯一显式测 Capacity/时序效率，但数据靠网盘、工程成熟度低。**注意不要与 LMEB 子任务 `mteb/MemBench`（979k 行的对话检索任务）混淆——两者仅同名，来源/任务/指标/数据全不同**
- **MemoryArena**（ICML 2026）：偏 agent 决策链，非纯记忆存储正确性
- **THUIR/MemoryBench**（ICML 2026）：28 数据集偏 continual learning，优先级低
- **MemGround**（arXiv 2604.14158，https://github.com/MorleyOlsen/MemGround）：游戏化三层记忆评测，独有 **MFCO（正确时间顺序片段比例）** 可量化「时序不漂移」；checkpoint 续跑机制是测容灾恢复的天然钩子。风险：仓库极新极小（3 star）、数据集规模未公布、工程成熟度低。备选关注。
- **memory-bench**（SKILL.md 格式本地评测工具，https://api.skillhub.cn/user_8a3569c1/memory-bench）：12 类问题、EM/F1 零配置、v2.4+ 可加载 LongMemEval/LoCoMo 数据。局限：非学术基准、安装细节无法公开验证、自报分数未复现。可作轻量回归工具，公信力弱。

**命名澄清**：用户说的「MTEB-LMEB / Long-term Memory Evaluation Benchmark」官方正名是
**LMEB: Long-horizon Memory Embedding Benchmark**（不是 Long-term Memory Evaluation，那是 LongMemEval 的全称）。
HF collection「Lychee-KaLM-LMEB & MTEB-LMEB」含 8 个任务：LoCoMo / LongMemEval / MemBench /
EPBench / KnowMeBench / REALTALK / TMD / MemGovern。MTEB 榜单条目名是 "Long-Horizon Memory"。

---

## 3. 我们独有的原创机会 ★★★

**调研结论：R1（无损迁移）/ R2（不遗忘）/ R3（容灾重建）/ 不漂移，没有任何现成基准覆盖。**

所有公开 benchmark 都只测「检索/回答质效」，没有一个测：
- 跨进程重载后逐字段/逐向量/逐检索结果一致
- 数据只增不删、失效只打标记
- 删光索引后从真值层确定性重建
- 容灾切换后的可用性

**建议**：把这个做成 fatfish-heart 的原创元基准并开源，填补行业空白。
我们已有证据：8000/8000=100%、余弦偏差 0、故障矩阵 10 类、异地副本恢复演练。

这正是「不是向量 RAG 概率召回，是内生自演化结构 + 确定性检索 + 容灾」的唯一证明方式。

---

## 4. 资助 / 认证 / 曝光平台（已核实）

### 4.1 优先级总表

| 平台 | 类型 | 门槛 | 中国个人可申请 | 建议 |
|---|---|---|---|---|
| OpenSSF Best Practices | 认证 badge | 无 | ✅ | **P0 立即做** |
| OpenSSF Scorecard | 自动安全检查 | 无 | ✅ | **P0 立即做** |
| GitHub topics | 曝光 | 无 | ✅ | **P0 立即做** |
| **awesome 列表投稿** | 外部背书链接 | **0 star 可投** | ✅ | **P1 现在就能做** |
| Product Hunt | 产品发布 | 需完整 demo | ✅ | P1（有 demo 后）|
| NLnet | 研发资助 | **见下方硬约束** | ❌ 基本不可 | **P2 有条件** |
| Sovereign Tech Fund | 基础设施投资 | 需采用证据 | 需核实 | 长期 |
| GSoC | 引入开发者 | 需组织资格 | 需核实 | 长期 |
| OpenSSF Technical Funding | 安全资助 | 需安全研究方向 | ✅ | 形成方向后 |
| GitHub Accelerator | 资金+导师 | **已停摆** | — | **不投入** |

### 4.2 NLnet：两条硬性淘汰条件（重要更正）

**之前把 NLnet 列为「P1 最现实的资金来源」是高估了。已从官方页面核实两条决定性事实：**

**① European Dimension 是硬性淘汰条件**
- 官方：https://nlnet.nl/NGI0/eu-dimension/ 、https://nlnet.nl/propose/
- 原文关键句：*"The European dimension refers to **the people contributing to the project**, not the project itself"*
- 要求：团队中来自 Horizon Europe 合资格国家的人，拿到 **≥60% 经费**
- **Horizon Europe 合资格名单里没有中国大陆、香港、台湾、澳门**
- 当前 call（2027-01-03 截止）主主题 Restack / CodeSupply 属 OIS 项目，受此约束
- **结论：中国大陆个人单独申请必被 knock-out 淘汰**，除非拉合资格国家合作者并让其拿 ≥60%
- 注：「世界任何人都能提交」与这条不矛盾——能提交 ≠ 能通过 European dimension 筛选

**② GenAI 政策 2.0（2026-10-20 生效）直接卡住这个项目**
- 官方：https://nlnet.nl/foundation/policies/generativeAI/2.0/
- **提案必须由本人手写**——不允许用生成式 AI 写提案，包括生成想法、重构、缩短、翻译
- 申请表要求 AI disclosure：用了什么模型、用途、粘贴 prompt 与输出（最多 8000 字）
- **不允许用生成式 AI 产出任何交付物，包括代码、设计和文档**
- 只允许 read-only 用途（如让 AI 检查代码找漏洞，但修复必须自己写），且须披露
- 风险：fatfish-heart 是 AI 记忆系统，且日常用 AI agent 写代码——按新政策可能被拒款
- 一次性豁免只适用 2026-10-21 前提交的提案，2027-01-03 这个 call 不适用

**③ Restack 基金明确排除 AI 项目（决定性）**

官方：https://nlnet.nl/restack/ 原文：
> "AI-related projects are not within scope — unless they are already widely used throughout society (> 1 million active human users) and directly relevant to the stack."

我们 0 star、刚起步 → **按主题直接排除，与欧洲维度无关，双重淘汰**。

唯一例外（Note 1）：非 GenAI 的自动化可能在范围内——确定性可复现的源码生成、形式化/符号证明、fuzz 测试、非 GenAI 机器学习。
若把项目严格定位为「确定性检索 + 可重建索引 + 形式化不变量（四红线）+ 非 GenAI」，理论可争取豁免，但项目实际定位是「AI 长期记忆系统」，认定风险极高，不能作主路径。

**当前所有 active funds 全景**（https://nlnet.nl/funding.html）：
Restack（排除GenAI）、CodeSupply（供应链，主题不符）、Open Social Fund（ActivityPub，不符）、
Research & Higher Education Technology（科研网络，不符）、GeoCommons（地理+排除GenAI，不符）——
**没有一个天然接收 AI 长期记忆项目**。

**最终结论（写死）**：NLnet 对本项目**基本不可行**，四重不符：
1. 当前基金无一主题契合
2. 主要基金明确排除 GenAI（100 万用户门槛）
3. 欧盟资助要求欧洲团队拿 ≥60% 经费
4. GenAI 政策 2.0 禁止 AI 代写提案/禁止 AI 生成代码文档作为可付款交付物——而本项目和用户开发方式正依赖 AI agent

仅在「彻底重构为非 GenAI 确定性记忆基础设施 + 找到欧洲合作者 + 本人手写提案 + 清理 AI 生成内容」全部满足时才重评，否则**直接跳过**。

### 4.3 GitHub Accelerator：已实质停摆

- 官网 https://accelerator.github.com/ 仍写 "GitHub Accelerator 2024"
- 申请页的 Apply now 仍指向 2024 年的 Google Form
- GitHub 博客该标签最后一文为 2024-10-02，无 2025/2026 届公告
- 最后一届：每项目 $40,000、10 周、截止 2024-03-05
- **结论：不投入精力**

### 4.4 现状

OpenSSF Scorecard 无需申请，本地可跑。仓库当前 stars=1、AGPL-3.0、
已有 CONTRIBUTING / LICENSE / PR 模板 / CI —— 满足 Best Practices 的基础项。

### 4.4b awesome 列表投稿：0 star 阶段唯一能立刻拿外部背书的渠道

**首选**：https://github.com/TeleAI-UAGI/Awesome-Agent-Memory （712 stars）
- CONTRIBUTING.md 明确规定：**<100 stars 的项目放进折叠的 "Emerging projects" 区块**
  —— 0 star 的 fatfish-heart 现在就能投，有专属位置，不会被 star 门槛拒绝
- 格式：star badge + code 链接 + 一行事实性描述（≤25 词）+ LF 换行，禁止营销语言
- 声明性能数据要注明来源（self-reported / paper-reported）
- 设有 **"🔒 Memory Security & Defense"** 分区 —— 我们的四红线/容灾/可重建正好归类于此
- 建议描述（客观、无营销）："Deterministic long-term memory for AI agents with lossless migration and rebuildable indexes"

**其他可投**（均无 star 门槛）：
- https://github.com/mnemoverse/awesome-agent-memory
- https://github.com/topoteretes/awesome-ai-memory （898 stars）
- https://github.com/trionlabs/awesome-ai-memory （明确欢迎 self-promotion）
- https://github.com/chenhuiyu/awesome-llm-memory
- https://github.com/DEEP-PolyU/Awesome-GraphMemory
- https://github.com/OpenDataBox/awesome-agent-memory

**这是 P1 里唯一今天就能做的**——提一个 PR 即可，不用等 demo、不用等比赛。

### 4.5 AML 标签演变（避免误读）

GitHub Issue #20（维护者 2026-10-16 回复）解释了榜单状态标签：
- Submitted (repo)：主办方代部署仓库（Cycle 1 路线）
- Submitted (API)：参赛方自部署端点（Cycle 2 唯一路线）
- Evaluated：平台完成的测试

**Cycle 2 起不再接受只交仓库**，`evaluation_route` 枚举里的
`maintainer_wrapped` 是历史上的代部署路线，对外已禁用。看到旧标签别误以为现在还能只交仓库。

### 4.6 AML 仓库里没有可用的参赛模板

data/*/pipeline.py 全部只实现 `answer` 和 `evaluate` 两个子命令，
读取 Search 返回的记忆做评分，**自身从不发起 Add/Search HTTP 请求**——
是纯平台侧下游评分代码，不能当参赛服务模板用。

评分细节（佐证「平台统一评分」）：
- LoCoMo：二元 CORRECT/WRONG judge（时间粒度必须严格一致、列表必须全包含）
- BEAM：0/0.5/1 rubric 逐条取均值；event_ordering 额外算 precision/recall/F1 与归一化 Kendall tau，final_score = tau_norm × f1
- judge 通过 OpenAI 兼容 `/chat/completions`，temperature=0，BEAM 默认 Qwen/Qwen3-14B

---

## 5. 执行顺序建议

| 阶段 | 做什么 | 门槛 |
|---|---|---|
| 现在 | LoCoMo 回归守护（已跑）+ LMEB 接入测 NDCG@10 | 无 |
| 现在 | MemoryAgentBench 冲突消解（R4 证明） | 需 OpenAI key |
| 现在 | **自建 R1-R3 元基准并开源** | 无，我们的独特价值 |
| 短期 | LongMemEval knowledge-update/abstention | 需 judge 模型 |
| 短期 | LongMemEval-V2 LAFS（延迟维度） | 需提交表单 |
| 中期 | OpenSSF Scorecard 跑分 + Badge | 无 |
| 中期 | 准备 AML 材料 + Add/Search 接口 | 等 Cycle 3 |
| 长期 | 资助平台申请（等代理报告） | 需采用证据 |

---

## 6. 思源写入

思源 MCP 当前断连，待恢复后把本文件写入 `系统/` 或 `收件箱/`，
标题建议 `肥鱼之心-真实测试与比赛规划`。
