
/**
 * cosmos-mem 插件 v3：两阶段记忆循环（用户核心设计）
 *
 * 用户原话：
 *   "在每次新会话开始的时候，查看上一次对话记录所留存的记忆，
 *    分析推测接下来用户会说的东西、应该可能要记忆什么；
 *    然后再看这次对话，对这次对话后的结论进行分析，
 *    哪些是需要记忆的，然后再记忆。"
 *
 * 两阶段：
 *   【阶段1 会话开始】agent/created
 *     读上次会话留存的高价值记忆 → 让模型推测"用户接下来可能要说什么、可能需记什么"
 *     → 结果注入上下文作为"预热"，并写进会话状态
 *
 *   【阶段2 对话后复盘】system-prompt/assemble
 *     取本会话最近 N 条用户输入 → 让模型复盘"哪些值得长期记住"
 *     → 写入记忆库（带 reason 与 importance）
 *
 * 选择权全在模型，不由规则决定。这是"模型自己迭代记忆"的实现。
 */
import { spawn } from 'node:child_process'
import { resolve, join } from 'node:path'
import { appendFileSync, statSync, renameSync } from 'node:fs'
import { createHash } from 'node:crypto'

// ============================================================
// Cordis 函数插件契约（官方规范）
//   必须导出：name / inject / Config / apply
//   禁止 default export（Loader 会 exports.default ?? exports，
//   有 default 会丢弃 inject/Config/apply）
// ============================================================

/** 插件名（Cordis 行 id 使用） */
export const name = 'cosmos-mem'

/** 需要的宿主服务 */
export const inject = ['tools', 'llm', 'webServer']

// ---------- 配置 Schema ----------
// 官方要求用 @deepseek-ai/schemastery（host-supplied peer）。
//
// E-240 修的关键 bug（"Cannot read properties of undefined (reading 'validate')"）：
//   本插件用 link: 安装成软链接。DSH 调用 Config 时用的是
//     schema['~standard'].validate(value)
//   所以 Config **必须是 Schemastery schema**，普通对象会让 ~standard 为 undefined，
//   直接抛这个 TypeError，整个插件启用失败。
//
//   原实现的问题是：只从一个基准（插件真实源码目录）解析 schemastery，
//   而 schemastery 装在 profile 的 node_modules 里，从
//   C:\Users\xuee\Desktop\肥鱼之心\dsh-plugin 解析不到 → _Schema=null
//   → Config 退化成普通对象 → 崩。
//
//   修法：用**多个候选基准**依次尝试解析，
//   包括 profile 目录（软链接安装时 schemastery 真正所在的地方）。
//   若全部失败，退回一个"最小可用 schema 形状"（含 ~standard.validate），
//   保证 DSH 不会因缺 ~standard 而崩。
import { createRequire } from 'node:module'
import { homedir } from 'node:os'
import { join as _join } from 'node:path'
// E-290：自主生命周期管理（拦截 turn-stopping，必要时 steer 续跑）
import { createLifecycleSupervisor } from './lifecycle.js'
// E-296：自重启（通过 configEditor / patch 文件触发 Loader reconcile）
import { createSelfReloader } from './selfreload.js'

function _loadSchemastery() {
  // 候选解析基准：插件自身 → 各 profile 目录
  const bases = [import.meta.url]
  try {
    const home = homedir()
    // DSH profile 的常见位置
    for (const p of [
      _join(home, '.dsh', 'profiles', 'desktop', 'package.json'),
      _join(home, '.dsh', 'profiles', 'web', 'package.json'),
      _join(home, '.dsh', 'profiles', 'default', 'package.json'),
    ]) {
      bases.push('file:///' + p.replace(/\\/g, '/'))
    }
  } catch { /* ignore */ }

  for (const base of bases) {
    try {
      const req = createRequire(base)
      const mod = req('@deepseek-ai/schemastery')
      const S = mod && (mod.default ?? mod)
      if (S && typeof S.object === 'function') return S
    } catch { /* try next */ }
  }
  return null
}

const _Schema = _loadSchemastery()

const DEFAULTS = {
  pythonPath: 'python',
  kernelDir: 'C:\\Users\\xuee\\Desktop\\肥鱼之心\\proto\\cosmos_mem',
  dbPath: 'C:\\Users\\xuee\\Desktop\\肥鱼之心\\proto\\cosmos_mem\\dsh_mem.db',
  autoInject: true,
  autoInjectTopK: 5,
  autoInjectMaxChars: 1500,
  // E-320：低于此分数的检索结果不注入（避免不相关记忆污染上下文）
  minInjectScore: 1.0,
  autoRemember: true,
  reviewWindow: 8,
  reviewProvider: 'workbuddy',
  reviewModel: 'deepseek-v4.1-flash',
  reviewMaxTokens: 900,
  minImportance: 0.5,
  minCost: 0.7,
  prewarm: true,
  prewarmSlots: 8,
  prewarmKernel: true,
  warmupWaitMs: 30000,
  debugLog: true,
  httpBase: '/cosmos-mem',
  lifecycle: { enabled: true, maxConsecutive: 20, requireProgress: true, debugLog: true },
}

export let Config
if (_Schema && typeof _Schema.object === 'function') {
  // 有 schemastery：给出完整 schema + 默认值
  Config = _Schema.object({
    pythonPath: _Schema.string().default(DEFAULTS.pythonPath),
    kernelDir: _Schema.string().default(DEFAULTS.kernelDir),
    dbPath: _Schema.string().default(DEFAULTS.dbPath),
    autoInject: _Schema.boolean().default(DEFAULTS.autoInject),
    autoInjectTopK: _Schema.number().default(DEFAULTS.autoInjectTopK),
    autoInjectMaxChars: _Schema.number().default(DEFAULTS.autoInjectMaxChars),
    minInjectScore: _Schema.number().default(DEFAULTS.minInjectScore),
    autoRemember: _Schema.boolean().default(DEFAULTS.autoRemember),
    reviewWindow: _Schema.number().default(DEFAULTS.reviewWindow),
    reviewProvider: _Schema.string().default(DEFAULTS.reviewProvider),
    reviewModel: _Schema.string().default(DEFAULTS.reviewModel),
    reviewMaxTokens: _Schema.number().default(DEFAULTS.reviewMaxTokens),
    minImportance: _Schema.number().default(DEFAULTS.minImportance),
    minCost: _Schema.number().default(DEFAULTS.minCost),
    prewarm: _Schema.boolean().default(DEFAULTS.prewarm),
    prewarmSlots: _Schema.number().default(DEFAULTS.prewarmSlots),
    prewarmKernel: _Schema.boolean().default(DEFAULTS.prewarmKernel),
    warmupWaitMs: _Schema.number().default(DEFAULTS.warmupWaitMs),
    debugLog: _Schema.boolean().default(DEFAULTS.debugLog),
    httpBase: _Schema.string().default(DEFAULTS.httpBase),
    // E-290：自主生命周期管理配置
    lifecycle: _Schema.object({
      enabled: _Schema.boolean().default(true),
      maxConsecutive: _Schema.number().default(20),
      requireProgress: _Schema.boolean().default(true),
      message: _Schema.string().default(''),
      debugLog: _Schema.boolean().default(true),
    }).default(DEFAULTS.lifecycle),
  })
} else {
  // 无 schemastery 的兜底（E-240）：
  //   绝不能导出普通对象——DSH 会访问 Config['~standard'].validate，
  //   普通对象没有 ~standard，直接抛
  //   "Cannot read properties of undefined (reading 'validate')"
  //   导致整个插件启用失败（这就是之前 404 的真因）。
  //
  //   这里构造一个"最小可用的 Standard Schema"：
  //   实现 ~standard.validate，把传入 config 与 DEFAULTS 合并后返回。
  //   这样 DSH 能正常验证，插件能启用，功能不受影响。
  Config = {
    '~standard': {
      version: 1,
      vendor: 'cosmos-mem-fallback',
      validate(value) {
        try {
          const merged = { ...DEFAULTS, ...(value || {}) }
          return { value: merged }
        } catch (e) {
          return { issues: [{ message: String(e && e.message) }] }
        }
      },
    },
    // 兼容某些代码直接用 .validate() 的情况
    validate(value) {
      return { ...DEFAULTS, ...(value || {}) }
    },
    // 兼容把 Config 当默认值读的情况
    ...DEFAULTS,
  }
}

/** 发送 JSON 响应 */
function sendJson(res, code, obj) {
  try {
    res.statusCode = code
    res.setHeader('Content-Type', 'application/json; charset=utf-8')
    res.setHeader('Cache-Control', 'no-store')
    res.end(JSON.stringify(obj))
  } catch (e) {
    try { res.statusCode = 500; res.end('{}') } catch { /* ignore */ }
  }
}

/** 读取请求体 */
function readBody(req) {
  return new Promise((resolve) => {
    let data = ''
    req.on('data', (c) => { data += c.toString('utf8') })
    req.on('end', () => {
      if (!data) return resolve({})
      try { resolve(JSON.parse(data)) } catch { resolve({}) }
    })
    req.on('error', () => resolve({}))
  })
}

/** 把一条记忆整理成 UI 需要的形状 */
function toUiItem(st, ep) {
  let scope = []
  let issues = []
  try {
    const s = st.scope_of(ep.ep_id)
    scope = Object.keys(s).filter((k) => s[k])
  } catch { /* ignore */ }
  try { issues = st.issues_of(ep.ep_id) } catch { /* ignore */ }
  return {
    ep_id: ep.ep_id,
    raw_text: ep.raw_text,
    cost: ep.cost,
    affect: ep.affect_peak,
    valence: ep.valence,
    created_at: ep.created_at,
    invalid_at: ep.invalid_at,
    superseded_by: ep.superseded_by,
    revision: ep.revision,
    scope,
    issues,
  }
}

// E-292：日志轮转
// 原来 appendFileSync 无限追加，实测涨到 5.1MB / 9900 行，
// 每组 scan-debug 约 1.5KB，会拖慢 assemble 并占满磁盘。
// 现在：超过 2MB 就轮转一次（保留当前文件为 .1，覆盖更旧的）。
const _LOG_MAX_BYTES = 2 * 1024 * 1024
let _logSizeChecked = 0

function debugLog(cfg, kind, data) {
  if (!cfg.debugLog) return
  try {
    const p = join(cfg.kernelDir, 'plugin-debug.jsonl')
    // 每 200 次检查一次大小（避免每次都 statSync）
    _logSizeChecked++
    if (_logSizeChecked % 200 === 1) {
      try {
        const st = statSync(p)
        if (st && st.size > _LOG_MAX_BYTES) {
          // 轮转：当前 → .1（覆盖旧的 .1）
          try { renameSync(p, p + '.1') } catch { /* ignore */ }
        }
      } catch { /* 文件不存在，忽略 */ }
    }
    appendFileSync(p,
      JSON.stringify({ t: new Date().toISOString(), kind, ...data }) + '\n', 'utf8')
  } catch { /* ignore */ }
}

/**
 * 会话状态：sid -> { prewarm, processed: Set<hash>, injected: bool, prewarmPromise }
 *
 * E-307 修的坑：原来是模块级 `const sessions = new Map()`。
 *   自重启（HMR 重载模块）后，模块被重新求值 → Map 被重置
 *   → `processed` 集合清空 → 同一批 user message 被重新识别
 *   → **同一件事被记了 2~3 次**（实测：12 组近义重复）。
 *
 * 修法：挂到 globalThis 上。globalThis 不随模块重载而重置，
 *       这样重载前后的"已处理"记录能接上。
 *
 * 这与 E-254 是同一条铁律的另一面：
 *   · 跨 apply() 要**丢弃**的状态（路由记录）→ 用 WeakMap 按 ctx 隔离
 *   · 跨 apply() 要**保留**的状态（已处理消息）→ 用 globalThis
 */
const SESSIONS_KEY = '__COSMOS_MEM_SESSIONS__'

function sessionsMap() {
  let m = globalThis[SESSIONS_KEY]
  if (!(m instanceof Map)) {
    m = new Map()
    try { globalThis[SESSIONS_KEY] = m } catch { /* ignore */ }
  }
  return m
}

function sessState(sid) {
  const sessions = sessionsMap()
  if (!sessions.has(sid)) {
    sessions.set(sid, {
      prewarm: null, processed: new Set(),
      injected: false, prewarmPromise: null,
    })
  }
  return sessions.get(sid)
}

/** 注入历史（内存，本次运行期间可见，供 UI「注入历史」展示） */
const injectHistory = []   // [{ at, sid, query, mode, items:[{ep_id,text}], prewarm }]
const MAX_INJECT_HISTORY = 50

/**
 * 提取最近 n 条真实用户消息（增量扫描版）
 *
 * 实测事件结构：
 *   e.type === 'user/message'
 *   e.data.content = [{type:'text', text:'...'}]
 *   e.data.source = { kind: 'user' | 'time-context' | ... }
 *
 * 性能优化：记录每个会话已处理到哪个 seq，只扫新增部分。
 * 旧版每轮全量扫 7000+ 条，随对话增长越来越慢。
 */
const scanCursor = new Map()   // sid -> lastSeq

function recentUserMessages(log, n, sid) {
  if (!Array.isArray(log) || n <= 0) return []

  // 增量：从上次扫到的位置开始（回退 2 条防止边界丢失）
  let start = 0
  const last = sid ? scanCursor.get(sid) : undefined
  if (typeof last === 'number' && last > 0) {
    start = Math.max(0, last - 2)
  }
  if (sid && log.length) {
    const tail = log[log.length - 1]
    if (tail && typeof tail.seq === 'number') scanCursor.set(sid, tail.seq)
  }

  const out = []
  for (let i = log.length - 1; i >= start && out.length < n; i--) {
    const e = log[i]
    if (!e || e.type !== 'user/message') continue
    const d = e.data
    if (!d) continue
    const src = d.source && d.source.kind
    if (src && src !== 'user') continue
    let text = ''
    if (typeof d.content === 'string') text = d.content
    else if (Array.isArray(d.content)) {
      text = d.content
        .filter((c) => c && c.type === 'text' && typeof c.text === 'string')
        .map((c) => c.text).join(' ')
    }
    text = (text || '').trim()
    if (text.startsWith('Time sampled while preparing')) continue
    if (text) out.push({ seq: e.seq, text })
  }
  return out.reverse()
}

/**
 * 常驻内核客户端（替代每次 spawn 新进程）
 *
 * 实测对比：
 *   旧方案 cli_mcp.py  每次约 1500-2000ms（每次重载 BGE）
 *   常驻方案稳态       约 13-18ms（模型只加载一次）
 *
 * 协议：每行一个 JSON
 *   请求 {"id":1,"cmd":"search","payload":{...}}
 *   响应 {"id":1,"ok":true,...}
 */
const kernelPools = new Map()   // dbPath -> { proc, pending: Map, nextId, ready }

function getKernel(cfg) {
  const key = cfg.dbPath
  let k = kernelPools.get(key)
  if (k && k.proc && !k.proc.killed) return k

  const script = resolve(cfg.kernelDir, 'kernel_server.py')
  const proc = spawn(cfg.pythonPath || 'python',
    ['-u', script, cfg.dbPath],
    {
      cwd: cfg.kernelDir,
      env: {
        ...process.env,
        PYTHONIOENCODING: 'utf-8',
        KMP_DUPLICATE_LIB_OK: 'TRUE',
      },
      stdio: ['pipe', 'pipe', 'pipe'],
    })

  k = { proc, pending: new Map(), nextId: 1, buffer: '', ready: false, startedAt: Date.now(), readyWaiters: [] }
  kernelPools.set(key, k)

  // 累积 stdout 按行切分
  proc.stdout.on('data', (chunk) => {
    k.buffer += chunk.toString('utf8')
    let idx
    while ((idx = k.buffer.indexOf('\n')) >= 0) {
      const line = k.buffer.slice(0, idx).trim()
      k.buffer = k.buffer.slice(idx + 1)
      if (!line) continue
      let resp = null
      try { resp = JSON.parse(line) } catch { continue }
      const id = resp && resp.id
      const waiter = k.pending.get(id)
      if (waiter) {
        k.pending.delete(id)
        clearTimeout(waiter.timer)
        waiter.resolve(resp)
      }
    }
  })

  proc.stderr.on('data', (d) => {
    const s = d.toString('utf8')
    if (s.includes('[kernel] ready')) {
      k.ready = true
      // 唤醒所有等待 ready 的请求
      for (const fn of k.readyWaiters) {
        try { fn() } catch { /* ignore */ }
      }
      k.readyWaiters = []
    }
  })

  proc.on('error', () => { kernelPools.delete(key) })
  proc.on('close', () => {
    for (const [, w] of k.pending) {
      clearTimeout(w.timer)
      w.reject(new Error('kernel exited'))
    }
    k.pending.clear()
    kernelPools.delete(key)
  })

  return k
}

/** 等待内核 ready（预热完成），最多等 maxMs */
function waitReady(k, maxMs) {
  if (k.ready) return Promise.resolve()
  return new Promise((resolvePromise) => {
    let done = false
    const finish = () => {
      if (done) return
      done = true
      resolvePromise()
    }
    k.readyWaiters.push(finish)
    setTimeout(finish, maxMs || 20000)
  })
}

/** 向常驻内核发一个请求 */
async function callKernel(cfg, command, payload, signal, timeoutMs) {
  const k = getKernel(cfg)
  // 首次调用会等预热完成，避免请求撞上模型加载导致 7 秒延迟
  await waitReady(k, cfg.warmupWaitMs ?? 20000)

  const id = k.nextId++
  return new Promise((resolvePromise, reject) => {
    const timer = setTimeout(() => {
      k.pending.delete(id)
      reject(new Error('kernel timeout ' + (timeoutMs || 30000) + 'ms'))
    }, timeoutMs || 30000)

    k.pending.set(id, { resolve: resolvePromise, reject, timer })

    if (signal) {
      signal.addEventListener('abort', () => {
        const w = k.pending.get(id)
        if (w) {
          clearTimeout(w.timer)
          k.pending.delete(id)
          reject(new Error('aborted'))
        }
      }, { once: true })
    }

    try {
      k.proc.stdin.write(JSON.stringify({ id, cmd: command, payload }) + '\n')
    } catch (e) {
      clearTimeout(timer)
      k.pending.delete(id)
      reject(new Error('kernel write failed: ' + (e && e.message)))
    }
  })
}

/** 关闭所有内核（插件卸载时调用） */
function shutdownKernels() {
  for (const [, k] of kernelPools) {
    try { k.proc.stdin.end() } catch { /* ignore */ }
    try { k.proc.kill() } catch { /* ignore */ }
  }
  kernelPools.clear()
}

/** 通用：调模型拿文本（非流式收集） */
async function callModel(ctx, cfg, system, user, maxTokens, signal) {
  const provider = cfg.reviewProvider
  const model = cfg.reviewModel
  if (!provider || !model) return { ok: false, error: 'no provider/model' }
  let text = ''
  try {
    const stream = ctx.llm.stream({
      provider, model,
      messages: [{ role: 'user', content: [{ type: 'text', text: user }] }],
      system,
      maxTokens: maxTokens ?? 900,
      temperature: 0,
      signal,
    })
    for await (const chunk of stream) {
      if (chunk && chunk.type === 'text-delta') text += chunk.text
    }
    return { ok: true, text }
  } catch (e) {
    return { ok: false, error: String(e && e.message), text }
  }
}

/**
 * LLM 精排：把检索到的候选交给模型，让模型挑出最相关的（批量比较）。
 *
 * E-191 实测：批量精排（一次看 N 条横向比较）命中率 99%（抽样），
 *   优于逐条判断 90.7%。E-208 实测：LLM 精排 ∪ cross-encoder 精排
 *   融合后命中率 88.1%（单独 79.1%/78.3%）。
 *
 * 参数：
 *   query   查询
 *   items   候选 [{ep_id, text, score}]
 *   top     最终返回条数
 *
 * 返回：命中的 ep_id 数组（按相关度降序），失败返回 null
 */
async function rerankWithLLM(cfg, ctx, query, items, top, signal) {
  if (!items || items.length === 0) return null
  try {
    const cand = items.map((it, i) => `${i}. ${it.text}`).join('\n')
    const system = [
      '你是记忆检索的精排器。只做判断，不输出多余内容。',
      '',
      '任务：给你一个【查询】和若干【候选记忆】，从候选中挑出最可能直接回答查询的条目。',
      '',
      '判断标准：',
      '  1. 查询问的是具体信息（谁/什么/何时/多少/哪里/为什么）',
      '  2. 找真正含答案的那条，不要被"同话题但无关细节"迷惑',
      '  3. 可以挑 1-3 条（答案可能分散在多条）',
      '',
      '只输出一个 JSON 数组（候选的序号），不要解释：',
      '[0, 2, 5]',
      '序号必须来自候选列表，按相关度降序。若没有相关的，输出空数组 []。',
    ].join('\n')
    const user = `查询：${query}\n\n候选：\n${cand}`
    const r = await callModel(ctx, cfg, system, user, 300, signal)
    if (!r.ok || !r.text) return null
    const arr = extractJson(r.text)
    if (!Array.isArray(arr)) return null
    const picks = arr.filter((n) => typeof n === 'number' && n >= 0 && n < items.length)
    return picks.map((n) => items[n].ep_id)
  } catch (e) {
    debugLog(cfg, 'rerank-fail', { error: String(e && e.message) })
    return null
  }
}

/**
 * 矛盾判定：把系统准备好的候选包交给模型，让模型判断
 * 「新记忆在谈论哪条旧记忆」+「是什么关系」。
 *
 * 职责划分（E-122 实测模型 14/14，规则最高 0.929）：
 *   · 系统已完成：余弦、值类型、值 token、候选筛选、扩散取数
 *   · 模型只做：选目标 + 判关系 + 决定要不要扩散
 *
 * 迭代扩散（E-132 实测，用户设计）：
 *   固定 top_k 在任何规模都会失效（1010→k10，5010→k50，10010→k100）。
 *   改为 10 → 20 → 40 → 80 → 160 按需扩散：
 *     所有规模命中率 100%，平均 1.0/1.5/1.9 轮、10/30/60 条候选，
 *     比固定 100 条省 90%/70%/40%。
 *
 * 参数：
 *   initialPack  第一轮候选包
 *   epId         记忆 id（扩散时要带回内核）
 *   callKernelFn 内核调用函数
 *
 * 返回 { raw, rounds, totalCands } 或 null
 */
async function judgeConflict(cfg, ctx, initialPack, epId, callKernelFn, signal) {
  const system = [
    '你是记忆系统的矛盾判定器。只做判断，不输出多余内容。',
    '',
    '任务：给你一条【新记忆】和若干【候选旧记忆】，判断它们的关系。',
    '',
    '第一步：选目标。candidates 里哪一条是"新记忆真正在谈论的旧记忆"？',
    '  余弦最高不等于就是目标。要看是否在说**同一件事的同一个槽位**。',
    '  例：新句说"截止日期"，候选里有"验收标准延迟5ms"（余弦略高）',
    '  和"项目15号截止"（余弦略低），目标是后者——前者说的是性能指标。',
    '  可以没有目标（新记忆谈的是全新的事）。',
    '',
    '第二步：判关系（只针对你选出的目标）：',
    '  duplicate = 同义重复。新记忆说的是同一件事，**没有带来任何新信息**，',
    '              只是措辞/标点/数字写法的不同（如"全部"vs"所有"、"3栋"vs"三栋"）',
    '  merge    = 有信息增益。新记忆是旧记忆的扩展，**带来了实质新信息**',
    '              （多了条件、指标、房间号、频次、额外要求等）；两条都有价值',
    '  replace  = 新记忆让旧值作废（换了/改了/搬家了/延期了）',
    '  conflict = 两者互相矛盾但都可能是对的，需要澄清（不同来源说法不一/记不清）',
    '  none     = 没有目标，新记忆说的是全新的事',
    '',
    'duplicate 与 merge 的判据（只问一个问题）：',
    '  新记忆里有没有旧记忆没有的信息？哪怕一个小细节？',
    '    · 有   → merge',
    '    · 完全没有，只是换说法 → duplicate',
    '  注意陷阱："15号截止，但可宽限三天" → merge（多了宽限条件，15号仍成立，不作废）',
    '             "接口低于200ms，并要求并发1000" → merge（多了并发指标）',
    '',
    '第三步：如果这些候选里**都没有**同一槽位的旧记忆，',
    '  但最高余弦仍然不低（说明确实像，只是没找对那条），',
    '  把 need_expand 设为 true —— 系统会给你更多候选再判断一次。',
    '  如果这些候选确实与主题无关（余弦都很低），need_expand 设为 false。',
    '',
    '只输出一个 JSON，不要解释、不要代码块：',
    '{"target":"<候选原文 或 null>","relation":"duplicate|replace|merge|conflict|none",',
    ' "need_expand":true|false,"merged_text":"<仅 merge 时填：融合双方全部信息的完整文本，否则null>",',
    ' "reason":"<一句话>","confidence":0-1}',
    'target 必须是 candidates 里某条的原文，不能改写，不能编造。',
    '若 relation=merge，merged_text 必须包含新旧两条的全部独有信息，不丢任何细节。',
  ].join('\n')

  const LADDER = [10, 20, 40, 80, 160]
  let pack = initialPack
  let curK = 10
  let rounds = 0
  let totalCands = 0
  let lastRaw = null

  while (true) {
    rounds += 1
    totalCands += (pack.candidates || []).length

    const user = JSON.stringify({
      new_memory: pack.new_memory,
      new_value_types: pack.new_value_types,
      new_value_tokens: pack.new_value_tokens,
      round: rounds,
      candidates: (pack.candidates || []).map((c) => ({
        text: c.text, cos: c.cos,
        value_types: c.value_types, value_tokens: c.value_tokens,
      })),
    }, null, 1)

    const r = await callModel(ctx, cfg, system, user, 400, signal)
    if (!r.ok || !r.text) {
      debugLog(cfg, 'judge-model-fail', { error: r.error, round: rounds })
      return lastRaw ? { raw: lastRaw, rounds, totalCands } : null
    }
    const j = extractJson(r.text)
    if (!j || !j.relation) {
      debugLog(cfg, 'judge-model-badjson',
               { text: String(r.text).slice(0, 200), round: rounds })
      return lastRaw ? { raw: lastRaw, rounds, totalCands } : null
    }
    lastRaw = JSON.stringify(j)

    // 模型说不用扩散 / 已经找到目标 → 停
    const wantExpand = j.need_expand === true
    const foundTarget = j.target && String(j.target).trim() &&
                        String(j.target).trim().toLowerCase() !== 'null'
    if (!wantExpand || foundTarget) break

    // 扩到下一档
    const nextK = LADDER.find((k) => k > curK)
    if (!nextK) {
      debugLog(cfg, 'judge-expand-maxed', { curK, rounds })
      break
    }

    let exp
    try {
      exp = await callKernelFn(cfg, 'expand', {
        ep_id: epId, prev_k: curK, next_k: nextK,
        expand_round: rounds + 1,
      }, signal, 20000)
    } catch (e) {
      debugLog(cfg, 'judge-expand-fail', { error: String(e && e.message) })
      break
    }
    if (!exp || !exp.ok || !exp.pack ||
        (exp.pack.candidates || []).length === 0) {
      debugLog(cfg, 'judge-expand-empty', { nextK })
      break
    }

    debugLog(cfg, 'judge-expanded', {
      round: rounds + 1, prev_k: curK, next_k: nextK,
      new_candidates: (exp.pack.candidates || []).length,
    })
    pack = exp.pack
    curK = nextK
  }

  return { raw: lastRaw, rounds, totalCands, finalK: curK }
}

/**
 * 链式展开：系统给候选，模型判断哪些属于同一情景链（E-143 实测 7/7）。
 *
 * 用户的定义（docs/49）：触发不是语义相似，是情景相似或情绪共振，而且会链式展开。
 *   "夕阳 → 小时候的夕阳 → 小时候的事"
 *
 * 为什么必须由模型判断（E-143 实测）：
 *   · 链式目标与 seed 的余弦只有 0.42~0.52（规则阈值 0.75 全被筛掉）
 *   · 余弦 Top-1 只能对 4/7；Top-3 只能对 6/7；模型 7/7
 *   · 模型自己的观察："余弦排序与同链几乎不相关，甚至反向"
 *
 * 参数：searchResult 已含直接命中结果
 * 返回：追加的链式条目数组
 */
async function expandChain(cfg, ctx, searchResult, query, callKernelFn, signal) {
  const items = (searchResult.results || []).filter((it) => it && it.ep_id)
  if (!items.length) return []
  const seed = items[0]

  // 第 1 步：系统准备候选
  const prep = await callKernelFn(cfg, 'chain_prepare', {
    seed_ep_id: seed.ep_id,
    query: query || '',
    top_n: 20,
  }, signal, 20000)
  if (!prep || !prep.ok || !prep.pack ||
      !(prep.pack.candidates || []).length) {
    return []
  }

  const system = [
    '你负责记忆的「链式展开」：由一条已命中的记忆，联想出同一段经历的其他细节。',
    '',
    '给你 seed（已命中的记忆）和候选池。请挑出**所有与 seed 属于同一情景/事件链**的候选。',
    '',
    '同链的判据：',
    '  · 描述的是同一件事 / 同一段经历 / 同一个场景的不同侧面',
    '  · 人在回忆 seed 时，会自然地想起它',
    '  · 因果承接（因为住院 → 所以很累）、时间承接（那天去了海边 → 那天吃了什么）、',
    '    场景承接（同一个地方、同一群人、同一个物件）',
    '',
    '不要选：',
    '  · 只是主题词面相似但实际是不同事件的（如"童年夏夜" vs "青岛海边"都不是同一段）',
    '  · 技术配置、无关的其他事',
    '',
    '注意：余弦高不代表同链，甚至常常相反。必须按语义判断。',
    '',
    '只输出 JSON，不要解释、不要代码块：',
    '{"chain":["<候选原文>","..."],"reason":"<一句话说明怎么连起来的>","confidence":0-1}',
    'chain 里每条必须是候选里某条的原文。没有同链的就输出 {"chain":[]}。',
  ].join('\n')

  const user = JSON.stringify({
    query: prep.pack.query,
    seed: prep.pack.seed,
    candidates: prep.pack.candidates,
  }, null, 1)

  const r = await callModel(ctx, cfg, system, user, 400, signal)
  if (!r.ok || !r.text) {
    debugLog(cfg, 'chain-model-fail', { error: r.error })
    return []
  }
  const j = extractJson(r.text)
  if (!j || !Array.isArray(j.chain) || j.chain.length === 0) {
    debugLog(cfg, 'chain-model-empty', { text: String(r.text).slice(0, 160) })
    return []
  }

  // 第 2 步：系统把模型选中的落库并返回
  const applied = await callKernelFn(cfg, 'chain_apply', {
    seed_ep_id: seed.ep_id,
    raw: JSON.stringify(j),
    query: query || '',
    reason: j.reason || '',
  }, signal, 20000)

  if (!applied || !applied.ok) return []

  return (applied.chain || []).map((c) => ({
    ep_id: c.ep_id,
    text: c.text,
    score: 0,
    chain: true,        // 标记：链式到达，不是直接命中
  }))
}

/** 从模型输出里抽 JSON */
function extractJson(text) {
  if (!text) return null
  const m = text.match(/\{[\s\S]*\}/)
  if (!m) return null
  try { return JSON.parse(m[0]) } catch { return null }
}

// ============================================================
// 阶段 1：会话开始，回顾上次记忆 + 推测接下来
// ============================================================
async function prewarmSession(ctx, cfg, sid, signal) {
  try {
    // 取高价值记忆（固定席位）作为"上次留存的记忆"
    const slots = await callKernel(cfg, 'search', {
      query: '', top: cfg.prewarmSlots ?? 8,
      fixed_slots: cfg.prewarmSlots ?? 8, min_cost: 0.5,
    })
    const items = slots.results || []
    if (!items.length) {
      debugLog(cfg, 'prewarm-skip', { why: 'no memories' })
      return null
    }

    const memList = items.map((it, i) => `${i + 1}. ${it.text}`).join('\n')

    const system =
      '你是记忆系统的"前瞻分析器"。根据上一次会话留存的记忆，' +
      '推测用户接下来可能会说什么、可能需要记住什么。\n\n' +
      '注意：这是推测，不是断言。目的是让系统提前准备好相关记忆。\n' +
      '只输出 JSON，不要解释。\n' +
      '格式：{"prediction":"一段话说明可能的话题与可能需要记忆的内容",' +
      '"likely_topics":["话题1","话题2"],"maybe_need":["可能需要记的内容1"]}\n' +
      '若记忆里看不出线索，输出 {"prediction":"无明显线索","likely_topics":[],"maybe_need":[]}'

    const user = `上次会话留存的记忆：\n${memList}\n\n请推测用户接下来可能说什么。`

    const r = await callModel(ctx, cfg, system, user, 500, signal)
    if (!r.ok) {
      debugLog(cfg, 'prewarm-error', { error: r.error })
      return null
    }
    const parsed = extractJson(r.text)
    if (!parsed) {
      debugLog(cfg, 'prewarm-parse-fail', { raw: (r.text || '').slice(0, 200) })
      return null
    }

    const st = sessState(sid)
    st.prewarm = {
      prediction: String(parsed.prediction || '').slice(0, 400),
      topics: Array.isArray(parsed.likely_topics) ? parsed.likely_topics.slice(0, 6) : [],
      maybeNeed: Array.isArray(parsed.maybe_need) ? parsed.maybe_need.slice(0, 6) : [],
      at: Date.now(),
    }
    debugLog(cfg, 'prewarm-ok', {
      prediction: st.prewarm.prediction.slice(0, 120),
      topics: st.prewarm.topics,
      memoryCount: items.length,
    })
    return st.prewarm
  } catch (e) {
    debugLog(cfg, 'prewarm-exception', { error: String(e && e.message) })
    return null
  }
}

// ============================================================
// 阶段 2：对话后复盘，判断哪些要记
// ============================================================
async function reviewAndRemember(ctx, cfg, sid, messages, signal) {
  if (!messages.length) return []
  const transcript = messages.map((m, i) => `[${i + 1}] ${m.text.slice(0, 500)}`).join('\n')

  const system =
    '你是记忆系统的"复盘分析器"。判断用户说过的内容里，哪些值得长期记住。\n\n' +
    '该记：\n' +
    '1. 长期有效的事实（密码、编号、地址、日期、参数、约定、承诺）\n' +
    '2. 用户的偏好、习惯、关系、身份信息\n' +
    '3. 影响未来决策的结论或决定\n' +
    '不该记：\n' +
    '1. 一次性的寒暄、问句、临时状态\n' +
    '2. 正在进行的操作（"帮我查一下""跑个测试"）\n' +
    '3. 已经记过的重复内容\n\n' +
    '重要：宁可少记，不要记垃圾。每条记忆要写成完整、独立可读的句子。\n' +
    '只输出 JSON，不要解释。\n' +
    '格式：{"memories":[{"text":"完整记忆","reason":"为什么值得记","importance":0.0到1.0}]}\n' +
    '若无值得记的，输出 {"memories":[]}'

  const user = `用户最近的 ${messages.length} 条输入：\n\n${transcript}\n\n请判断哪些值得长期记住。`

  const r = await callModel(ctx, cfg, system, user, cfg.reviewMaxTokens ?? 900, signal)
  if (!r.ok) {
    debugLog(cfg, 'review-error', { error: r.error })
    return []
  }
  const parsed = extractJson(r.text)
  if (!parsed || !Array.isArray(parsed.memories)) {
    debugLog(cfg, 'review-parse-fail', { raw: (r.text || '').slice(0, 250) })
    return []
  }

  const out = []
  for (const it of parsed.memories) {
    const t = (it && it.text ? String(it.text) : '').trim()
    const imp = Number(it && it.importance)
    if (!t || t.length < 4) continue
    if (Number.isFinite(imp) && imp < (cfg.minImportance ?? 0.5)) continue
    out.push({
      text: t,
      reason: it.reason ? String(it.reason).slice(0, 150) : '',
      importance: Number.isFinite(imp) ? imp : 0.6,
    })
  }
  debugLog(cfg, 'review-ok', {
    count: out.length, total: parsed.memories.length,
    provider: cfg.reviewProvider, model: cfg.reviewModel,
    raw: (r.text || '').slice(0, 200),
  })
  return out
}

function formatMemories(items, maxChars, minScore = 0) {
  if (!Array.isArray(items) || items.length === 0) {
    return { text: '', skipped: 0, kept: 0 }
  }
  const lines = []
  let total = 0
  let skipped = 0
  for (const it of items) {
    // E-320：低分结果不注入
    //
    // 实测问题：查询「Docker 容器」（库里没有相关内容）时，
    // 检索仍返回 5 条"矮子里拔将军"的结果，最高分只有 0.88。
    // 这些不相关的记忆会污染上下文，让模型误以为用户提到过这些。
    //
    // minScore 由调用方传入，默认 0（不过滤，保持旧行为）。
    const sc = typeof it.score === 'number' ? it.score : null
    if (minScore > 0 && sc !== null && sc < minScore) {
      skipped++
      continue
    }
    const line = '- [' + it.ep_id + '] ' + it.text + (it.note ? '  (' + it.note + ')' : '')
    if (total + line.length > maxChars) break
    lines.push(line)
    total += line.length
  }
  return { text: lines.join('\n'), skipped, kept: lines.length }
}

/**
 * 格式化 L0/L1 强制记忆（E-231 改进）。
 *
 * 关键洞察：『强制在场』≠『每轮全文』。
 *   · 首轮：给全文（模型建立完整认知基线）
 *   · 后续：只列标题 + ep_id（提醒存在，需要时用 mem_search 取全文）
 *
 * E-230 诊断：9 条 L0/L1 共 1997 字，若每轮全文注入，
 * 会吃掉 autoInjectMaxChars(1500) 的全部预算，用户自己的记忆挤不进来。
 */
function formatProtected(items, full) {
  if (!Array.isArray(items) || items.length === 0) return ''
  const lines = []
  for (const it of items) {
    const title = it.note || '准则'
    if (full) {
      lines.push(`- [${it.ep_id}] ${title}：${it.text}`)
    } else {
      // E-294：摘要模式再精简
      //
      // 原来给「标题 + 正文前 24 字…」，
      // 11 条合计 570 字，每轮都发送。
      // 但那 24 字预览并不能让模型理解准则内容
      // （看到"红线：记忆系统四条不可动摇的红…"仍然不知道具体是什么），
      // 反而占了 264 字。
      //
      // 现在只给「标题」，模型看到标题知道有这条准则，
      // 需要细节时用 mem_search 查。11 条约 306 字。
      lines.push(`- ${title}`)
    }
  }
  return lines.join('\n')
}

/** 已注册的 HTTP 路由 —— 按 ctx 实例隔离
 *
 *  E-254 修的坑：
 *    原来用模块级 `const REGISTERED_ROUTES = new Set()`，
 *    跨 apply() 调用一直存在。但 DSH 重载插件时，
 *    模块可能没重新初始化（缓存），而宿主**已经重建了 webServer**，
 *    路由表是空的。
 *    于是新 Set 里记着"这些路由都注册过了"，
 *    全部 skip，一条都没真注册 → 所有端点 404。
 *    日志特征：http-route-summary ok=0, skip=14, fail=0
 *
 *  修法：
 *    · 用 WeakMap 按 ctx 实例隔离（ctx 换 = 新 webServer = 重新注册）
 *    · ctx 被回收时记录自动释放，不会泄漏
 *    · 注册成功后写入；失败不写入（下次还能重试）
 */
const REGISTERED_BY_CTX = new WeakMap()

function routesOf(ctx) {
  let s = REGISTERED_BY_CTX.get(ctx)
  if (!s) {
    s = new Set()
    REGISTERED_BY_CTX.set(ctx, s)
  }
  return s
}

/**
 * E-298 修的坑：重载时 ctx 不变 → 路由全部被 skip
 *
 * 日志证据（自重启第一次验证）：
 *   07:01:25  http-route-summary ok=0, skip=17, fail=0
 *
 * 根因：
 *   · 自重启（configEditor reconcile）触发时，**ctx 对象是同一个**
 *   · 但上一轮的 ctx.effect 被 dispose → webServer 上的路由被移除了
 *   · 而 REGISTERED_BY_CTX 里仍记着"17 个都注册过了"
 *   · 于是全部 skip → 路由表空了 → 所有端点 404
 *
 * 与 E-254 是同一类错误的不同触发路径：
 *   E-254：模块级 Set 跨进程残留
 *   E-298：按 ctx 隔离的 Set 跨 apply() 残留（ctx 没换，但 effect 被清理）
 *
 * 修法：
 *   · 每次 apply() 开始时，把本次 key 记到 ctx 上
 *   · 如果发现是**新的 apply()**（key 变了），就清空该 ctx 的记录
 *   · 这样重载时会重新注册，而同一次 apply 内部仍能去重
 */
const APPLY_KEY_BY_CTX = new WeakMap()
// E-298：本次加载周期的唯一标记。apply() 内联调用一次，避免中途误重置。
let _applySeq = 0

function resetRoutesForNewApply(ctx, myKey) {
  const prev = APPLY_KEY_BY_CTX.get(ctx)
  if (prev !== myKey) {
    // 新的 apply()：旧 effect 已被 dispose，路由也没了，必须重注册
    APPLY_KEY_BY_CTX.set(ctx, myKey)
    REGISTERED_BY_CTX.delete(ctx)
    return true   // 表示"重置过"
  }
  return false
}

export function apply(ctx, config) {
  // E-302：重载指纹
  //
  // 用途：验证自重启（mem_selfreload）是否真的重新执行了模块代码，
  //      而不是只 reconcile 配置。
  // 每次 apply() 都会重新计算这个指纹并挂到 globalThis，
  // /selfreload 的 diagnose 会读它。
  // 指纹变化 = 代码真的重载了。
  try {
    globalThis.__COSMOS_RELOAD_MARKER__ = 'apply@' +
      new Date().toISOString() + '#' + Math.random().toString(36).slice(2, 8)
  } catch { /* ignore */ }

  const cfg = { ...DEFAULTS, ...(config || {}) }

  // E-298：每次 apply() 都是新的加载周期，旧的 ctx.effect 已被 dispose，
  // webServer 上的路由已被移除，必须清空去重记录重新注册。
  try {
    _applySeq += 1
    resetRoutesForNewApply(ctx, _applySeq)
  } catch (e) { /* ignore */ }

  // ================= 管理界面 HTTP API =================
  // Client 通过 fetch 调用这些端点读取/操作记忆
  const base = cfg.httpBase || '/cosmos-mem'

  /** 统一的请求处理（exact 与 prefix 共用） */
  const httpHandler = async (req, res) => {
    const url = new URL(req.url || '/', 'http://local')
    const sub = url.pathname.slice(base.length) || '/'
    const method = (req.method || 'GET').toUpperCase()

    try {
      if (sub === '/list' || sub === '/list/') {
        const query = url.searchParams.get('q') || ''
        const top = Number(url.searchParams.get('top') || 200)
        const includeInvalid = url.searchParams.get('includeInvalid') === '1'
        const r = await callKernel(cfg, 'list', { query, top, includeInvalid }, undefined, 20000)
        return sendJson(res, 200, r)
      }
      if (sub === '/invalidate' && method === 'POST') {
        const body = await readBody(req)
        const r = await callKernel(cfg, 'invalidate', body, undefined, 10000)
        return sendJson(res, 200, r)
      }
      if (sub === '/revise' && method === 'POST') {
        const body = await readBody(req)
        const r = await callKernel(cfg, 'revise', body, undefined, 20000)
        return sendJson(res, 200, r)
      }
      if (sub === '/stats' || sub === '/stats/') {
        const r = await callKernel(cfg, 'stats', {}, undefined, 10000)
        return sendJson(res, 200, r)
      }
      // 配置读写：读当前运行时配置，写运行时覆盖（不持久化，重启恢复 cordis.patch.yml）
      if (sub === '/config' && method === 'GET') {
        return sendJson(res, 200, {
          ok: true,
          config: {
            autoInject: !!cfg.autoInject,
            autoInjectTopK: cfg.autoInjectTopK,
            autoInjectMaxChars: cfg.autoInjectMaxChars,
            autoRemember: !!cfg.autoRemember,
            reviewWindow: cfg.reviewWindow,
            minImportance: cfg.minImportance,
            minCost: cfg.minCost,
            debugLog: !!cfg.debugLog,
            prewarm: !!cfg.prewarm,
            prewarmSlots: cfg.prewarmSlots,
            reviewProvider: cfg.reviewProvider,
            reviewModel: cfg.reviewModel,
          },
        })
      }
      if (sub === '/config' && method === 'POST') {
        const body = await readBody(req)
        // 只允许覆盖可运行时调整的白名单字段
        const writable = ['autoInject', 'autoInjectTopK', 'autoInjectMaxChars',
          'autoRemember', 'reviewWindow', 'minImportance', 'minCost',
          'debugLog', 'prewarm', 'prewarmSlots']
        const before = {}
        const changed = {}
        for (const k of writable) {
          if (k in body) {
            before[k] = cfg[k]
            let v = body[k]
            if (typeof cfg[k] === 'number') v = Number(v)
            if (typeof cfg[k] === 'boolean') v = !!v
            cfg[k] = v
            changed[k] = v
          }
        }
        debugLog(cfg, 'config-write', changed)
        // E-266：配置变更写入审计（可追溯），失败不影响主流程
        if (Object.keys(changed).length) {
          try {
            await callKernel(cfg, 'config', {
              action: 'log', old: before, new: changed, source: 'ui',
            }, undefined, 8000)
          } catch (e) {
            debugLog(cfg, 'config-audit-fail', { error: String(e && e.message) })
          }
        }
        return sendJson(res, 200, { ok: true, changed })
      }
      // 注入历史（只读，供 UI「注入历史」Tab）
      if (sub === '/inject-history' && method === 'GET') {
        return sendJson(res, 200, { ok: true, history: injectHistory })
      }
      // 睡眠整理历史（只读，来自内核 meta 表 sleep:log）
      if (sub === '/sleep-history' && method === 'GET') {
        const r = await callKernel(cfg, 'sleep_history', {}, undefined, 10000)
        return sendJson(res, 200, { ok: true, history: (r && r.history) || [] })
      }
      // 审计日志（只读，来自内核 audit_log 表）
      if (sub === '/audit' && method === 'GET') {
        const kind = url.searchParams.get('kind') || ''
        const sid = url.searchParams.get('sid') || ''
        const r = await callKernel(cfg, 'audit',
          { kind: kind || null, sid: sid || null, limit: 200 }, undefined, 10000)
        return sendJson(res, 200, { ok: true, logs: (r && r.logs) || [], stats: (r && r.stats) || {} })
      }
      // 分层保护（L0~L4）
      if (sub === '/hook' && method === 'GET') {
        const r = await callKernel(cfg, 'hook', { action: 'list' }, undefined, 10000)
        return sendJson(res, 200, {
          ok: true,
          protected: (r && r.protected) || [],
          stats: (r && r.stats) || {},
          levels: (r && r.levels) || {},
        })
      }
      if (sub === '/hook' && method === 'POST') {
        const body = await readBody(req)
        const r = await callKernel(cfg, 'hook', body, undefined, 10000)
        return sendJson(res, 200, r)
      }
      // 种子记忆（系统自带的通用认知基线）
      if (sub === '/seed' && method === 'GET') {
        const r = await callKernel(cfg, 'seed', { action: 'list' }, undefined, 10000)
        return sendJson(res, 200, r)
      }
      if (sub === '/seed' && method === 'POST') {
        const body = await readBody(req)
        const r = await callKernel(cfg, 'seed', body, undefined, 60000)
        return sendJson(res, 200, r)
      }
      // 笔记（记忆系统的另一半：知识沉淀）
      if (sub === '/note' && method === 'GET') {
        const q = url.searchParams.get('q') || ''
        const topic = url.searchParams.get('topic') || ''
        const tier = url.searchParams.get('tier') || ''
        const r = q
          ? await callKernel(cfg, 'note', { action: 'search', query: q, tier }, undefined, 15000)
          : await callKernel(cfg, 'note', { action: 'list', topic, tier }, undefined, 15000)
        return sendJson(res, 200, r)
      }
      if (sub === '/note' && method === 'POST') {
        const body = await readBody(req)
        const r = await callKernel(cfg, 'note', body, undefined, 20000)
        return sendJson(res, 200, r)
      }
      // 待办
      if (sub === '/todo' && method === 'GET') {
        const r = await callKernel(cfg, 'todo', {
          action: 'list',
          include_done: url.searchParams.get('include_done') === '1',
        }, undefined, 15000)
        return sendJson(res, 200, r)
      }
      if (sub === '/todo' && method === 'POST') {
        const body = await readBody(req)
        const r = await callKernel(cfg, 'todo', body, undefined, 15000)
        return sendJson(res, 200, r)
      }
      // 知识册装配（三段式：程序合并 → 模型重构）
      if (sub === '/compose' && method === 'GET') {
        const r = await callKernel(cfg, 'compose',
          { action: 'list_topics', tier: 'scrap', min_items: 3 }, undefined, 15000)
        return sendJson(res, 200, r)
      }
      if (sub === '/compose' && method === 'POST') {
        const body = await readBody(req)
        // digest 阶段是纯程序，快；finalize 是写库，也快
        const r = await callKernel(cfg, 'compose', body, undefined, 30000)
        return sendJson(res, 200, r)
      }
      // 知识整理提示（机械扫描）
      if (sub === '/knowledge-hint' && method === 'GET') {
        const r = await callKernel(cfg, 'knowledge_hint', {}, undefined, 15000)
        return sendJson(res, 200, r)
      }
      // 分诊
      if (sub === '/triage' && method === 'GET') {
        const r = await callKernel(cfg, 'triage',
          { action: 'stats' }, undefined, 20000)
        return sendJson(res, 200, r)
      }
      // 配置变更历史（E-266）
      if (sub === '/config-history' && method === 'GET') {
        const r = await callKernel(cfg, 'config',
          { action: 'history', limit: 50 }, undefined, 15000)
        return sendJson(res, 200, r)
      }
      // 自重启（E-296）
      if (sub === '/selfreload' && method === 'GET') {
        if (!selfReloader) {
          return sendJson(res, 200, { ok: false, error: 'reloader 未初始化' })
        }
        return sendJson(res, 200, { ok: true, ...selfReloader.diagnose() })
      }
      if (sub === '/selfreload' && method === 'POST') {
        if (!selfReloader) {
          return sendJson(res, 200, { ok: false, error: 'reloader 未初始化' })
        }
        const body = await readBody(req)
        // E-304：用 reloadVerified，返回 reloaded 字段说明代码是否真的重载
        const r = await selfReloader.reloadVerified(body || {})
        return sendJson(res, 200, r)
      }
      return sendJson(res, 404, { ok: false, error: 'unknown endpoint ' + sub })
    } catch (e) {
      debugLog(cfg, 'http-error', { sub, error: String(e && e.message) })
      return sendJson(res, 500, { ok: false, error: String(e && e.message) })
    }
  }

  // 显式注册 exact 路由（比 prefix 更可靠，也便于排错）
  //
  // E-239：DSH 的 webServer.register 遇重复 (kind,path) 会直接 throw，
  //        而 apply() 可能被多次调用，第二次会让后续路由全注册失败。
  // E-254：去重记录必须**按 ctx 隔离**（见 routesOf 的注释）。
  //        模块级 Set 会在宿主重建 webServer 后仍记着"已注册"，
  //        导致全部 skip、一条都不注册 → 所有端点 404。
  const routes = ['/list', '/invalidate', '/revise', '/stats', '/config', '/inject-history', '/sleep-history', '/audit', '/hook', '/seed', '/note', '/todo', '/compose', '/triage', '/knowledge-hint', '/config-history', '/selfreload']
  let routeOk = 0
  let routeSkip = 0
  let routeFail = 0
  const registered = routesOf(ctx)
  for (const r of routes) {
    const full = base + r
    if (registered.has(full)) {
      routeSkip++
      continue
    }
    try {
      ctx.effect(() => ctx.webServer.register({
        kind: 'exact',
        path: full,
        handler: httpHandler,
      }))
      registered.add(full)
      routeOk++
      debugLog(cfg, 'http-route-ok', { path: full })
    } catch (e) {
      routeFail++
      const msg = String(e && e.message)
      debugLog(cfg, 'http-route-fail', { path: full, error: msg })
      // 也写 stderr：debugLog 可能因配置关闭而丢失，stderr 一定可见
      try { console.error('[cosmos-mem] 路由注册失败', full, msg) } catch { /* ignore */ }
    }
  }
  debugLog(cfg, 'http-route-summary', { ok: routeOk, skip: routeSkip, fail: routeFail })

  // ================= 启动即预热 =================
  // 插件加载时立刻拉起内核并加载 BGE，避免第一次对话撞上 7 秒冷启动
  if (cfg.prewarmKernel !== false) {
    try {
      const k = getKernel(cfg)
      waitReady(k, 30000)
        .then(() => debugLog(cfg, 'kernel-ready', { ms: Date.now() - k.startedAt }))
        .catch(() => { /* ignore */ })
    } catch (e) {
      debugLog(cfg, 'kernel-start-error', { error: String(e && e.message) })
    }
  }

  // ================= 显式工具 =================

  ctx.tools.register({
    name: 'mem_add',
    description: '写入一条记忆。用于用户明确要求记住的事实、约定、密码、偏好、承诺等。请把内容写完整。',
    parameters: {
      type: 'object',
      properties: {
        text: { type: 'string', description: '记忆内容（尽量完整）' },
        atmosphere: { type: 'string', description: '可选：当时的氛围/场景' },
        explicit: { type: 'boolean', description: '用户是否明确强调记住' },
      },
      required: ['text'],
    },
    output: {
      schema: { type: 'object' },
      render: (_a, v) => [{
        type: 'text',
        text: '已写入记忆 ' + v.ep_id + '\n能回答：' +
              ((v.scope || []).join('/') || '无') + '\n缺陷：' +
              ((v.issues || []).join('/') || '无'),
      }],
    },
    async execute(args, runCtx) {
      // E-268：judge_affect=true 让内核带上情绪判断的 prompt
      //（模型 MAE 0.0598 vs 规则 0.2702，E-152 实测）
      const r = await callKernel(cfg, 'add',
        { ...args, judge: true, judge_affect: true },
        runCtx && runCtx.signal)

      // ---- 情绪判断（模型版）----
      // 原来插件从未传 judge_affect，导致系统一直走规则降级路径。
      if (r && r.ok && r.need_affect && r.affect_prompt) {
        try {
          const p = r.affect_prompt
          const ar = await callModel(ctx, cfg, p.system, p.user, 300,
                                     runCtx && runCtx.signal)
          if (ar && ar.ok && ar.text) {
            r.affect = await callKernel(cfg, 'affect', {
              ep_id: r.ep_id,
              raw: ar.text,
            }, runCtx && runCtx.signal)
            debugLog(cfg, 'affect-done', {
              ep_id: r.ep_id,
              affect: r.affect && r.affect.affect,
              valence: r.affect && r.affect.valence,
            })
          }
        } catch (e) {
          debugLog(cfg, 'affect-fail', { error: String(e && e.message) })
        }
      }

      // 系统预筛认为值得判断 → 调模型判断（含按需扩散），再把结果送回内核落盘
      if (r && r.ok && r.need_judge && r.pack) {
        try {
          const judged = await judgeConflict(cfg, ctx, r.pack, r.ep_id,
                                             callKernel, runCtx && runCtx.signal)
          if (judged && judged.raw) {
            r.judge = await callKernel(cfg, 'judge', {
              ep_id: r.ep_id,
              raw: judged.raw,
              top_k: judged.finalK || 10,
            }, runCtx && runCtx.signal)
            debugLog(cfg, 'judge-done', {
              ep_id: r.ep_id,
              relation: r.judge && r.judge.relation,
              target: r.judge && r.judge.target_text,
              rounds: judged.rounds,
              total_cands: judged.totalCands,
              final_k: judged.finalK,
            })
          }
        } catch (e) {
          debugLog(cfg, 'judge-fail', { error: String(e && e.message) })
        }
      } else if (r && r.need_judge === false) {
        debugLog(cfg, 'judge-skip', { reason: r.judge_skip_reason })
      }
      return r
    },
  })

  ctx.tools.register({
    name: 'mem_search',
    description: '检索记忆。返回最相关的记忆条目，并标注每条能回答或答不出哪类问题。chain=true 时会额外做链式联想（由 seed 联想到同一段经历的其他细节）。rerank=true 时用 cross-encoder（GPU 精排）重排结果，提升排序精度（E-207 实测命中率 78.3%，仅 156ms）。',
    parameters: {
      type: 'object',
      properties: {
        query: { type: 'string', description: '查询内容' },
        top: { type: 'number', description: '返回条数，默认 5' },
        scoped: { type: 'boolean', description: '是否按问题维度路由' },
        chain: { type: 'boolean', description: '是否做链式展开（会额外调一次模型，默认 false）' },
        rerank: { type: 'boolean', description: '是否用 cross-encoder 精排（GPU 加速，默认 false）' },
        rerank_llm: { type: 'boolean', description: '是否用 LLM 精排（批量比较候选，默认 false，多一次模型调用）' },
      },
      required: ['query'],
    },
    output: {
      schema: { type: 'object' },
      render: (_a, v) => {
        const items = (v && v.results) || []
        if (items.length === 0) return [{ type: 'text', text: '（没有找到相关记忆）' }]
        return [{
          type: 'text',
          text: items.map((it, i) => (i + 1) + '. [' + it.ep_id + '] ' + it.text +
            (it.chain ? '\n   (链式联想)' : '') +
            (it.note ? '\n   (' + it.note + ')' : '')).join('\n'),
        }]
      },
    },
    async execute(args, runCtx) {
      const r = await callKernel(cfg, 'search', args, runCtx && runCtx.signal)
      // 链式展开（E-143 实测：系统给候选 + 模型判链 7/7；余弦 Top-1 只有 4/7）
      // 只对高价值场景启用：默认关闭，需显式 chain=true
      if (args && args.chain && r && r.ok &&
          (r.results || []).length > 0) {
        try {
          const chain = await expandChain(cfg, ctx, r, args.query,
                                          callKernel, runCtx && runCtx.signal)
          if (chain && chain.length) {
            r.results = (r.results || []).concat(chain)
            r.chain_count = chain.length
            debugLog(cfg, 'chain-done', {
              query: args.query, added: chain.length,
            })
          }
        } catch (e) {
          debugLog(cfg, 'chain-fail', { error: String(e && e.message) })
        }
      }
      // LLM 精排（E-208 融合：LLM ∪ cross-encoder = 88.1%，比单独 79.1%/78.3% 都高）
      // 只在显式 rerank_llm=true 时启用（多一次模型调用）
      if (args && args.rerank_llm && r && r.ok &&
          (r.results || []).length > 0) {
        try {
          const items = r.results
          const llmPicks = await rerankWithLLM(cfg, ctx, args.query,
                                               items, args.top || 5,
                                               runCtx && runCtx.signal)
          if (llmPicks && llmPicks.length) {
            // 融合：LLM 选中的排在前面（保持原顺序），其余跟随
            const pickSet = new Set(llmPicks)
            const picked = items.filter((it) => pickSet.has(it.ep_id))
            const rest = items.filter((it) => !pickSet.has(it.ep_id))
            r.results = picked.concat(rest)
            r.rerank_llm_count = llmPicks.length
            debugLog(cfg, 'rerank-llm-done', {
              query: args.query, picked: llmPicks.length,
            })
          }
        } catch (e) {
          debugLog(cfg, 'rerank-llm-fail', { error: String(e && e.message) })
        }
      }
      return r
    },
  })

  ctx.tools.register({
    name: 'mem_revise',
    description: '修订一条已有记忆（保留历史）。',
    parameters: {
      type: 'object',
      properties: {
        ep_id: { type: 'string', description: '记忆 ID' },
        new_text: { type: 'string', description: '修正后的完整内容' },
      },
      required: ['ep_id', 'new_text'],
    },
    output: {
      schema: { type: 'object' },
      render: (_a, v) => [{ type: 'text', text: v && v.ok ? '已修订' : '未找到' }],
    },
    async execute(args, runCtx) {
      return await callKernel(cfg, 'revise', args, runCtx && runCtx.signal)
    },
  })

  ctx.tools.register({
    name: 'mem_invalidate',
    description: '标记一条记忆失效（数据不删除）。',
    parameters: {
      type: 'object',
      properties: {
        ep_id: { type: 'string', description: '记忆 ID' },
        superseded_by: { type: 'string', description: '可选：取代它的新内容' },
      },
      required: ['ep_id'],
    },
    output: {
      schema: { type: 'object' },
      render: (_a, v) => [{
        type: 'text',
        text: v && v.ok ? ('已标记失效' + (v.new_ep_id ? '，新记忆 ' + v.new_ep_id : '')) : '未找到',
      }],
    },
    async execute(args, runCtx) {
      return await callKernel(cfg, 'invalidate', args, runCtx && runCtx.signal)
    },
  })

  ctx.tools.register({
    name: 'mem_stats',
    description: '查看记忆库统计。',
    parameters: { type: 'object', properties: {} },
    output: {
      schema: { type: 'object' },
      render: (_a, v) => [{
        type: 'text',
        text: Object.entries(v || {}).map(([k, x]) => k + ': ' + x).join('\n'),
      }],
    },
    async execute(args, runCtx) {
      return await callKernel(cfg, 'stats', args, runCtx && runCtx.signal)
    },
  })

  ctx.tools.register({
    name: 'mem_sleep',
    description: '睡眠/巩固记忆：当一个任务或项目告一段落、一轮密集对话结束时，主动调用。系统会整理碎片（合并重复）、强化重要记忆、遗忘过时内容。像人睡觉时大脑自动巩固记忆。',
    parameters: { type: 'object', properties: {} },
    output: {
      schema: { type: 'object' },
      render: (_a, v) => [{
        type: 'text',
        text: v && v.ok
          ? '睡眠整理完成：合并 ' + ((v.result && v.result.merged && v.result.merged.length) || 0) +
            ' 对、遗忘 ' + ((v.result && v.result.invalidated && v.result.invalidated.length) || 0) +
            ' 条、强化 ' + ((v.result && v.result.bumped && v.result.bumped.length) || 0) + ' 条'
          : '睡眠整理：' + (v && v.error ? v.error : '无需整理'),
      }],
    },
    async execute(args, runCtx) {
      // 第 1 步：系统机械准备候选对（增量窗口 + L3 候选）
      const prep = await callKernel(cfg, 'sleep_prepare', {}, runCtx && runCtx.signal)
      const nPairs = (prep && prep.pairs ? prep.pairs.length : 0)
      const l3 = (prep && prep.l3_candidates) || []
      if (!prep || !prep.ok || (nPairs === 0 && l3.length === 0)) {
        return {
          ok: true,
          result: { merged: [], invalidated: [], bumped: [] },
          note: '没有发现需要整理的记忆',
        }
      }

      // 第 2 步：让模型判断（合并碎片 / 退役过时 L3 / 强化重要）
      const system = [
        '你是记忆系统的"睡眠整理器"。在任务告一段落时，整理这段时间累积的记忆。',
        '',
        '你可能拿到两类材料：',
        '  【A. 候选相似对】系统算出的可能相关的记忆对',
        '  【B. 阶段性记忆候选(L3)】可能已经过时的记忆',
        '',
        '三种处理方式：',
        '  merge      两条是"同一件事的碎片"，合并成一条完整的。',
        '             合并后 keep 那条要包含两条的全部信息。',
        '  invalidate 某条已经过时/被推翻/不再成立，标记失效（遗忘）。',
        '             既可用于"成对材料里作废的那条"，也可用于 B 类的 L3 候选。',
        '  bump       本阶段的关键记忆，值得以后更常想起（channel 用 recall_by_fixed）。',
        '',
        '判断 L3 是否过时的依据（谨慎，宁可留着也别误删）：',
        '  · 内容已被更新的信息取代（如旧的阶段结论已推进）',
        '  · 明确是一次性/临时性的记录，任务已完结',
        '  · 与当前仍有效的记忆直接矛盾',
        '  若只是"时间久了"但内容仍然成立，应当保留。',
        '',
        '重要：merge 和 invalidate 都只是"打标记"，不会真正删除数据，可随时追溯。',
        '',
        '只输出 JSON，不要解释：',
        '{"actions":[',
        '  {"op":"merge","keep_id":"<保留>","drop_id":"<并入>","merged_text":"<合并后完整文本>"},',
        '  {"op":"invalidate","ep_id":"<作废>"},',
        '  {"op":"bump","ep_id":"<强化>","channel":"recall_by_fixed","n":1}',
        ']}',
        '若都不需要处理，输出 {"actions":[]}',
      ].join('\n')

      const parts = []
      if (nPairs > 0) {
        const pairsText = (prep.pairs || []).map((p, i) =>
          `${i}. A[${p.a_id}] ${p.a_text}\n   B[${p.b_id}] ${p.b_text}\n   (余弦 ${p.cos})`
        ).join('\n\n')
        parts.push('【A. 候选相似对】\n\n' + pairsText)
      }
      if (l3.length) {
        const l3Text = l3.map((c, i) =>
          `${i}. [${c.ep_id}] (已存在 ${c.age_h} 小时) ${c.text}`
        ).join('\n\n')
        parts.push('【B. 阶段性记忆候选(L3)，判断是否已过时】\n\n' + l3Text)
      }

      const user = parts.join('\n\n') +
        '\n\n请判断哪些该 merge、哪些该 invalidate（遗忘/退役）、哪些该 bump（强化）。'

      const r = await callModel(ctx, cfg, system, user, 900, runCtx && runCtx.signal)
      if (!r.ok) return { ok: false, error: r.error }
      const parsed = extractJson(r.text)
      if (!parsed || !Array.isArray(parsed.actions)) {
        return { ok: false, error: '模型输出无法解析' }
      }

      // 第 3 步：落盘（并把本次判定过的对记下来，下次跳过）
      const apply = await callKernel(cfg, 'sleep_apply',
        {
          actions: parsed.actions,
          judged_pairs: (prep.pairs || []).map((p) => ({
            a_id: p.a_id, b_id: p.b_id,
          })),
        }, runCtx && runCtx.signal)
      debugLog(cfg, 'sleep-done', {
        actions: (parsed.actions || []).length,
        result: apply && apply.result,
      })
      return apply
    },
  })

  ctx.tools.register({
    name: 'mem_hook',
    description: '记忆分层保护（L0~L4）。L0 不可变（红线）、L1 常驻准则（每次对话生效）、L2 设计基石（核心洞察）——这三层永远不可失效/合并。查看哪些记忆受保护，或把一条重要记忆提升到保护层。',
    parameters: {
      type: 'object',
      properties: {
        action: {
          type: 'string',
          description: 'list=查看受保护记忆; check=查某条的层级; lock=设为保护; unlock=解除保护',
        },
        ep_id: { type: 'string', description: '目标记忆 ID（check/lock/unlock 需要）' },
        level: { type: 'string', description: '保护层级：L0(不可变)/L1(常驻)/L2(设计基石)' },
        note: { type: 'string', description: '可选说明' },
      },
      required: ['action'],
    },
    output: {
      schema: { type: 'object' },
      render: (_a, v) => {
        if (!v || !v.ok) return [{ type: 'text', text: '操作失败：' + ((v && v.error) || '未知错误') }]
        if (v.protected) {
          const lines = v.protected.map((p) =>
            `[${p.level} ${p.level_name}] ${(p.text || '').slice(0, 50)}`)
          const s = v.stats || {}
          return [{
            type: 'text',
            text: `共 ${s.total || 0} 条受保护记忆` +
              (s.by_level ? `（L0=${s.by_level.L0 || 0} L1=${s.by_level.L1 || 0} L2=${s.by_level.L2 || 0}）` : '') +
              '\n' + lines.join('\n'),
          }]
        }
        if (v.level_name !== undefined) {
          return [{
            type: 'text',
            text: '层级: ' + v.level_name +
              '\n可失效: ' + (v.can_invalidate ? '是' : '否') +
              '\n可合并: ' + (v.can_merge ? '是' : '否') +
              '\n可修订: ' + (v.can_revise ? '是' : '否') +
              '\n强制在场: ' + (v.always_present ? '是' : '否'),
          }]
        }
        return [{ type: 'text', text: JSON.stringify(v) }]
      },
    },
    async execute(args, runCtx) {
      return await callKernel(cfg, 'hook', args, runCtx && runCtx.signal)
    },
  })

  ctx.tools.register({
    name: 'mem_seed',
    description: '查看系统自带的种子记忆（通用的认知基线：红线、方法论、职责划分、认知纪律、系统自述等）。这些是系统的一部分，不是用户个人数据，恢复出厂后仍会保留。',
    parameters: {
      type: 'object',
      properties: {
        action: {
          type: 'string',
          description: 'list=查看种子内容; stats=统计',
        },
      },
      required: ['action'],
    },
    output: {
      schema: { type: 'object' },
      render: (_a, v) => {
        if (!v || !v.ok) return [{ type: 'text', text: '失败：' + ((v && v.error) || '未知') }]
        if (v.seeds) {
          const lines = v.seeds.map((s) =>
            `[${s.level}] ${s.title} (${s.chars}字)`)
          return [{
            type: 'text',
            text: `种子记忆 ${v.seeds.length} 条（系统自带，通用无隐私）\n` + lines.join('\n'),
          }]
        }
        return [{ type: 'text', text: JSON.stringify(v) }]
      },
    },
    async execute(args, runCtx) {
      return await callKernel(cfg, 'seed', args, runCtx && runCtx.signal)
    },
  })

  ctx.tools.register({
    name: 'mem_trace',
    description: '追溯一条记忆的完整演化链（A→B→C）。当你想知道某个信息是被取代过几次、历史上各版本是什么、最新版本是哪条时使用。',
    parameters: {
      type: 'object',
      properties: {
        ep_id: { type: 'string', description: '链上任意一条记忆的 ID' },
      },
      required: ['ep_id'],
    },
    output: {
      schema: { type: 'object' },
      render: (_a, v) => {
        if (!v || !v.ok) return [{ type: 'text', text: '追溯失败：' + ((v && v.error) || '未知') }]
        const lines = (v.chain || []).map((x, i) =>
          `${i + 1}. ${x.is_current ? '[当前]' : '[已失效]'} ${String(x.text || '').slice(0, 60)}`)
        return [{
          type: 'text',
          text: `演化链 ${v.length} 个版本：\n` + lines.join('\n'),
        }]
      },
    },
    async execute(args, runCtx) {
      return await callKernel(cfg, 'trace', args, runCtx && runCtx.signal)
    },
  })

  ctx.tools.register({
    name: 'mem_note',
    description: '知识沉淀（三层：随笔→笔记→手册）。记忆记录"我经历过什么"，笔记沉淀"我总结出了什么"。常用→记在记忆；少见但重要→写成详细笔记；少见又不重要→简短随笔。攒够同类可升层，最终整理成手册。action: add/get/revise/promote/list/search/suggest/suggest_scrap/suggest_manual。',
    parameters: {
      type: 'object',
      properties: {
        action: { type: 'string', description: 'add | get | revise | promote | children | list | search | stats | suggest（记忆→笔记）| suggest_scrap（随笔→笔记）| suggest_manual（笔记→手册）' },
        tier: { type: 'string', description: 'scrap 随笔（零散灵光）| note 笔记（按主题整理）| manual 手册（体系化成书）' },
        title: { type: 'string', description: '标题' },
        body: { type: 'string', description: '正文，Markdown' },
        topic: { type: 'string', description: '主题分类，如 sqlite / python / 项目架构' },
        tags: { type: 'array', items: { type: 'string' }, description: '标签' },
        source_ep_ids: { type: 'array', items: { type: 'string' }, description: '由哪些记忆整理而来' },
        source_note_ids: { type: 'array', items: { type: 'string' }, description: '由哪些笔记整理而来' },
        parent_id: { type: 'string', description: '手册的章节指向手册 note_id' },
        order_idx: { type: 'number', description: '章节顺序' },
        note_id: { type: 'string', description: '笔记 ID' },
        to_tier: { type: 'string', description: 'promote 的目标层级' },
        query: { type: 'string', description: '搜索关键词' },
      },
      required: ['action'],
    },
    output: {
      schema: { type: 'object' },
      render: (_a, v) => {
        if (!v || !v.ok) return [{ type: 'text', text: '知识库操作失败：' + ((v && v.error) || '未知') }]
        if (v.note) {
          const n = v.note
          return [{ type: 'text', text: `[${n.tier_name}] ${n.title}（${n.chars}字，rev${n.revision}）\n${String(n.body).slice(0, 250)}` }]
        }
        if (v.notes) {
          const s = v.stats || {}
          const groups = { scrap: [], note: [], manual: [] }
          v.notes.forEach((n) => { (groups[n.tier] || groups.note).push(n) })
          const out = []
          if (groups.scrap.length) out.push('【随笔】\n' + groups.scrap.map((n) => `· ${n.title}`).join('\n'))
          if (groups.note.length) out.push('【笔记】\n' + groups.note.map((n) => `· ${n.title}（${n.chars}字）`).join('\n'))
          if (groups.manual.length) out.push('【手册】\n' + groups.manual.map((n) => `· ${n.title}`).join('\n'))
          return [{ type: 'text', text: `随笔 ${s.scrap || 0} / 笔记 ${s.note || 0} / 手册 ${s.manual || 0}\n\n` + out.join('\n\n') }]
        }
        if (v.children) {
          const lines = v.children.map((c) => `${c.order_idx}. ${c.title}`)
          return [{ type: 'text', text: `章节（${v.children.length}）\n` + lines.join('\n') }]
        }
        if (v.topics) {
          const stage = v.stage || ''
          const lines = v.topics.map((t) => `· ${t.topic}：${t.count} 条，建议整理成${t.suggest_tier === 'manual' ? '手册' : '笔记'}`)
          return [{ type: 'text', text: (stage ? `[${stage}] ` : '') + (lines.length ? '建议：\n' + lines.join('\n') : '暂无建议') }]
        }
        return [{ type: 'text', text: JSON.stringify(v).slice(0, 500) }]
      },
    },
    async execute(args, runCtx) {
      return await callKernel(cfg, 'note', args, runCtx && runCtx.signal)
    },
  })

  ctx.tools.register({
    name: 'mem_todo',
    description: '待办清单（模型自己的任务队列，完全自主控制）。支持：add 新增、list 列表、done 完成、update 更新。kind=note_hint 用于标记"这块该整理成笔记了"。',
    parameters: {
      type: 'object',
      properties: {
        action: { type: 'string', description: 'add | list | done | update | stats' },
        title: { type: 'string', description: '待办标题（add）' },
        detail: { type: 'string', description: '详细说明（add/update）' },
        kind: { type: 'string', description: 'task 普通任务 | note_hint 该整理成笔记 | review 定期回顾' },
        priority: { type: 'number', description: '优先级，越大越靠前' },
        todo_id: { type: 'string', description: '待办 ID（done/update）' },
        status: { type: 'string', description: 'pending | doing | done | dropped' },
        include_done: { type: 'boolean', description: 'list 时是否包含已完成' },
      },
      required: ['action'],
    },
    output: {
      schema: { type: 'object' },
      render: (_a, v) => {
        if (!v || !v.ok) return [{ type: 'text', text: '待办操作失败：' + ((v && v.error) || '未知') }]
        if (v.todo) {
          const t = v.todo
          return [{ type: 'text', text: `[${t.todo_id.slice(0, 8)}] ${t.title}（${t.status}，${t.kind}）` }]
        }
        if (v.todos) {
          const lines = v.todos.map((t) => `[${t.status === 'done' ? '✓' : ' '}] ${t.title}${t.kind === 'note_hint' ? ' 📝' : ''}`)
          const s = v.stats || {}
          return [{ type: 'text', text: `待办 ${v.todos.length} 条（待办 ${s.pending || 0} / 进行中 ${s.doing || 0} / 已完成 ${s.done || 0}）\n` + lines.join('\n') }]
        }
        if (v.stats) {
          return [{ type: 'text', text: JSON.stringify(v.stats) }]
        }
        return [{ type: 'text', text: JSON.stringify(v).slice(0, 500) }]
      },
    },
    async execute(args, runCtx) {
      return await callKernel(cfg, 'todo', args, runCtx && runCtx.signal)
    },
  })

  ctx.tools.register({
    name: 'mem_compose',
    description: '把攒够的随笔装订成手册。三段式：①程序把同主题随笔机械合并成册子（零上下文）②你只负责定大纲/分类/排序/标注关联——不重写内容 ③程序按你的结构原样组装成手册。所以你的输出量很小，只是结构方案，原文一字不改。action: list_topics（看哪些主题够格）| digest（只做程序合并）| compose（全流程）',
    parameters: {
      type: 'object',
      properties: {
        action: { type: 'string', description: 'list_topics | digest | compose' },
        topic: { type: 'string', description: '主题（compose/digest 需要）' },
        tier: { type: 'string', description: '源层级，默认 scrap 随笔' },
        min_items: { type: 'number', description: '最少条目数，默认 3' },
        title: { type: 'string', description: '可选：指定手册标题' },
      },
      required: ['action'],
    },
    output: {
      schema: { type: 'object' },
      render: (_a, v) => {
        if (!v || !v.ok) return [{ type: 'text', text: '装订失败：' + ((v && v.error) || '未知') }]
        if (v.topics) {
          const lines = v.topics.map((t) => `· ${t.topic}：${t.count} 条随笔，可以装订成册`)
          return [{ type: 'text', text: lines.length ? '可装订的主题：\n' + lines.join('\n') : '暂无可装订的主题' }]
        }
        if (v.manual) {
          const ol = v.outline || {}
          const secs = (ol.outline || []).map((s, i) => `${i + 1}. ${s.section}（${(s.items || []).length} 条）`)
          return [{
            type: 'text',
            text: `手册已生成：《${v.manual.title}》${v.manual.chars} 字\n` +
              `来源 ${(v.source_note_ids || []).length} 条随笔，正文由程序原样组装（未改写）\n\n` +
              `大纲：\n${secs.join('\n')}`,
          }]
        }
        if (v.stage === 'digest_ready') {
          return [{ type: 'text', text: `原始册子已生成（${v.n_items} 条，${v.digest_chars} 字）。下一步只需给它定大纲。\n\n${String(v.digest).slice(0, 500)}` }]
        }
        return [{ type: 'text', text: JSON.stringify(v).slice(0, 500) }]
      },
    },
    async execute(args, runCtx) {
      const act = args.action || 'list_topics'
      if (act === 'list_topics') {
        return await callKernel(cfg, 'compose', { action: 'list_topics', ...args }, runCtx && runCtx.signal)
      }
      if (act === 'digest') {
        return await callKernel(cfg, 'compose', { action: 'digest', ...args }, runCtx && runCtx.signal)
      }
      // compose 全流程：程序合并 → 模型定大纲 → 程序组装
      const dg = await callKernel(cfg, 'compose',
        { action: 'digest', topic: args.topic, tier: args.tier || 'scrap', min_items: args.min_items || 3 },
        runCtx && runCtx.signal)
      if (!dg || !dg.ok) return dg || { ok: false, error: '合并失败' }
      if (dg.stage !== 'digest_ready') return dg

      // 模型只输出结构方案（JSON），不写正文
      const p = dg.prompt || {}
      const r = await callModel(ctx, cfg, p.system, p.user, 2000, runCtx && runCtx.signal)
      if (!r.ok) return { ok: false, error: '模型定大纲失败：' + r.error }

      // 交给内核解析结构 + 程序组装
      return await callKernel(cfg, 'compose', {
        action: 'finalize',
        topic: args.topic,
        title: args.title || '',
        outline_raw: r.text,
        items: dg.items || [],
        note_ids: dg.note_ids || [],
      }, runCtx && runCtx.signal)
    },
  })

  ctx.tools.register({
    name: 'mem_triage',
    description: '记忆分诊：用现有数据决定"该记在哪"。常用度看访问计数，重要性看 cost。四象限：常用+重要→留记忆；少见+重要→留记忆且写详细笔记；少见+不重要→写简短随笔。action: stats | all | suggest | one',
    parameters: {
      type: 'object',
      properties: {
        action: { type: 'string', description: 'stats | all | suggest | one' },
        ep_id: { type: 'string', description: '单条记忆 ID（one）' },
      },
      required: ['action'],
    },
    output: {
      schema: { type: 'object' },
      render: (_a, v) => {
        if (!v || !v.ok) return [{ type: 'text', text: '分诊失败：' + ((v && v.error) || '未知') }]
        if (v.stats) {
          const s = v.stats
          return [{ type: 'text', text: `记忆分诊（共 ${s.total} 条）\n` +
            `· 常用+重要（留记忆）：${s.counts.core}\n` +
            `· 常用+不重要（留记忆）：${s.counts.common}\n` +
            `· 少见+重要（该写详细笔记）：${s.counts.critical}\n` +
            `· 少见+不重要（该写随笔）：${s.counts.minor}` }]
        }
        if (v.counts) {
          return [{ type: 'text', text: `四象限：core=${v.counts.core} common=${v.counts.common} critical=${v.counts.critical} minor=${v.counts.minor}` }]
        }
        if (v.suggestions) {
          const lines = v.suggestions.map((s) =>
            `· ${s.quadrant} ${s.count} 条 → 写${s.suggest_tier === 'note' ? '详细笔记' : '简短随笔'}（${s.reason}）`)
          return [{ type: 'text', text: lines.length ? '建议：\n' + lines.join('\n') : '暂无建议' }]
        }
        if (v.quadrant) {
          return [{ type: 'text', text: `[${v.quadrant}] cost=${v.cost} 访问=${v.visits}\n策略：${v.strategy}` }]
        }
        return [{ type: 'text', text: JSON.stringify(v).slice(0, 400) }]
      },
    },
    async execute(args, runCtx) {
      return await callKernel(cfg, 'triage', args, runCtx && runCtx.signal)
    },
  })

  // ================= 自主生命周期管理（E-290）=================
  //
  // 目的：解决"模型写完总结就停下"的问题。
  //
  // 已从 DSH 实际代码验证的关键事实（不是文档推测）：
  //   dsh-agent-loop 的回合循环里：
  //     if (turnEnds && this.inbox.nextStep.length === 0) {
  //         await dispatch.serial("agent/turn-stopping", {...})
  //     }
  //     if (turnEnds && this.inbox.nextStep.length === 0) break; // 仍空才结束
  //     target = "next-step";                                     // 否则继续
  //   agent.steer(input) 正是往 inbox.nextStep 推消息。
  //   → 在 turn-stopping 事件里 steer，回合就不会结束。
  //
  // 安全边界（防止无限空转）：最大次数 / 进展检测 / 同 turn 防重入。
  try {
    // 注意：debugLog 的签名是 (cfg, kind, data)，
    // lifecycle 里调用的是 log(kind, data)，所以这里要包一层适配。
    const lcLog = (kind, data) => debugLog(cfg, kind, data)
    const sup = createLifecycleSupervisor(cfg, lcLog)
    sup.attach(ctx)
    ctx.set('cosmosMemLifecycle', sup)
  } catch (e) {
    console.error('[cosmos-mem] lifecycle attach failed:', e && e.message)
  }

  // ================= 自重启（E-296）=================
  //
  // 目的：改完插件代码后能自己重载，不用让用户重启 DSH。
  //
  // 依据（已从 cordis_inspect_query 实测确认）：
  //   · configEditor.edit(entry, change) 会走 Loader reconciliation，
  //     返回说明是 "Fulfillment after Loader reconciliation completes."
  //   · 事件 'app-boot/config-reload' 存在，说明配置变更能在运行中生效
  //   · RestartReason 有 'no-hmr'，反过来说明有 HMR 的路径
  let selfReloader = null
  try {
    const lcLog = (kind, data) => debugLog(cfg, kind, data)
    selfReloader = createSelfReloader(ctx, cfg, lcLog)
  } catch (e) {
    console.error('[cosmos-mem] selfreload init failed:', e && e.message)
  }

  ctx.tools.register({
    name: 'mem_selfreload',
    description: '让记忆插件自己重载（改完插件代码后不用重启 DSH）。action=diagnose 先看能不能重载；action=reload 尝试重载。原理：通过 configEditor 触发 Cordis Loader 的 reconcile，或改 cordis.patch.yml 让 watchdog 检测。',
    parameters: {
      type: 'object',
      properties: {
        action: { type: 'string', description: 'diagnose | reload' },
        strategy: { type: 'string', description: 'configEditor（默认）| patch' },
      },
      required: ['action'],
    },
    output: {
      schema: { type: 'object' },
      render: (_a, v) => {
        if (!v) return [{ type: 'text', text: '无返回' }]
        if (v.ok && v.entryFound !== undefined) {
          return [{
            type: 'text',
            text: `自重启诊断：\n` +
              `  configEditor: ${v.hasConfigEditor ? '✅' : '❌'}\n` +
              `  pluginManager: ${v.hasPluginManager ? '✅' : '❌'}\n` +
              `  找到我的 entry: ${v.entryFound ? '✅ ' + JSON.stringify(v.entry) : '❌ ' + (v.reason || '')}\n` +
              `  entries 总数: ${v.entriesTotal}\n` +
              (v.sample ? `  样例: ${JSON.stringify(v.sample)}\n` : ''),
          }]
        }
        if (v.ok) {
          // E-304：区分"触发成功"和"代码真的重载了"
          const strat = v.tried
            ? v.tried.filter(t => t.ok).map(t => t.strategy).join(',')
            : v.path
          if (v.reloaded === true) {
            return [{
              type: 'text',
              text: `✅ 代码已真正重载（策略 ${strat}）\n`
                + `  指纹: ${v.markerBefore} → ${v.markerAfter}`,
            }]
          }
          if (v.reloaded === false) {
            return [{
              type: 'text',
              text: `⚠ 触发了 reconcile，但**代码没有重载**（策略 ${strat}）\n`
                + `  指纹未变: ${v.markerBefore}\n`
                + `  原因: ${v.hint || ''}`,
            }]
          }
          return [{
            type: 'text',
            text: `重载已触发（策略 ${strat}）${v.hint ? '：' + v.hint : ''}`,
          }]
        }
        return [{
          type: 'text',
          text: `重载失败：${JSON.stringify(v.tried || v)}`.slice(0, 500),
        }]
      },
    },
    async execute(args, runCtx) {
      if (!selfReloader) {
        return { ok: false, error: 'reloader 未初始化' }
      }
      if (args.action === 'diagnose') {
        return { ok: true, ...selfReloader.diagnose() }
      }
      // E-304：用 reloadVerified，避免「返回 ok 但代码没重载」的误判
      return await selfReloader.reloadVerified({ strategy: args.strategy })
    },
  })

  // ================= 阶段 1：会话开始预判 =================

  ctx.on('agent/created', async (payload) => {
    try {
      const sid = payload && payload.agent && payload.agent.id
      if (!sid) return
      if (cfg.prewarm === false) return
      const key = String(sid)
      const st0 = sessState(key)
      // 异步跑，不阻塞会话创建；但把 promise 存起来，第一次 assemble 时等它
      st0.prewarmPromise = prewarmSession(ctx, cfg, key, payload.signal)
        .catch((e) => { debugLog(cfg, 'prewarm-catch', { error: String(e && e.message) }); return null })
    } catch (e) {
      debugLog(cfg, 'agent-created-error', { error: String(e && e.message) })
    }
  })

  // ================= 阶段 2：对话复盘 + 注入 =================

  ctx.on('system-prompt/assemble', async (assembly, context, next) => {
    // 入口无条件日志：先确认这个事件到底有没有被触发
    try {
      const scope0 = context && context.scope
      const sess0 = scope0 && scope0.session
      const log0 = sess0 && sess0.log
      debugLog(cfg, 'assemble-enter', {
        hasContext: !!context,
        hasScope: !!scope0,
        scopeKeys: scope0 ? Object.keys(scope0).slice(0, 6) : [],
        hasSession: !!sess0,
        logType: Array.isArray(log0) ? ('array:' + log0.length) : typeof log0,
      })
    } catch (e0) {
      debugLog(cfg, 'assemble-enter-error', { error: String(e0 && e0.message) })
    }

    const result = await next()
    try {
      const scope = context && context.scope
      const sess = scope && scope.session
      const log = sess && sess.log
      const sid = scope && scope.id ? String(scope.id) : ''
      const st = sessState(sid)

      let st_diag_counter = st._diagCounter || 0
      const recent = recentUserMessages(log, cfg.reviewWindow ?? 8, sid)
      const latest = recent.length ? recent[recent.length - 1].text : ''
      // 诊断：如果 recent 为空，把真实的 user/message 事件结构打出来
      // E-292：这条诊断日志曾经每轮都触发（因为 lifecycle 消息的结构与
      //        预期不同，导致 recent 一直为空），每组打印 ~1.5KB，
      //        日志暴涨到 2469 条，拖慢了 assemble。
      //        现在只在「首次或每 50 次」打一次，避免刷屏。
      if (!recent.length && Array.isArray(log)) {
        st_diag_counter = (st_diag_counter || 0) + 1
        if (st_diag_counter === 1 || st_diag_counter % 50 === 0) {
          const samples = []
          let umCount = 0
          for (let i = log.length - 1; i >= 0 && samples.length < 2; i--) {
            const e = log[i]
            if (e && e.type === 'user/message') {
              umCount++
              samples.push({
                keys: Object.keys(e),
                dataKeys: e.data ? Object.keys(e.data) : null,
                dataShape: JSON.stringify(e.data || {}).slice(0, 200),
              })
            }
          }
          const typeSet = new Set()
          for (let i = Math.max(0, log.length - 200); i < log.length; i++) {
            const e = log[i]
            if (e && e.type) typeSet.add(e.type)
          }
          debugLog(cfg, 'scan-debug', {
            nth: st_diag_counter,
            foundUserMessage: umCount, samples,
            recentTypes: Array.from(typeSet).slice(0, 15),
          })
        }
        // 写回计数器（否则每次都从 0 开始，退化成每次都打印）
        st._diagCounter = st_diag_counter
      }
      debugLog(cfg, 'assemble-scan', {
        sid: sid.slice(0, 40), recentCount: recent.length,
        latest: latest.slice(0, 50),
        logLen: Array.isArray(log) ? log.length : -1,
      })

      // ---- 阶段2：复盘并记忆（按"消息集合 hash"去重，异步不阻塞）----
      if (cfg.autoRemember !== false && recent.length >= 1) {
        const h = createHash('md5')
          .update(sid + '|' + recent.map((r) => r.seq).join(','))
          .digest('hex')
        if (!st.processed.has(h)) {
          st.processed.add(h)
          reviewAndRemember(ctx, cfg, sid, recent, undefined)
            .then(async (mems) => {
              for (const mem of mems) {
                try {
                  const r = await callKernel(cfg, 'add', {
                    text: mem.text, explicit: false,
                  })
                  debugLog(cfg, 'auto-remember-model', {
                    text: mem.text.slice(0, 90), reason: mem.reason,
                    importance: mem.importance, ep_id: r && r.ep_id,
                  })
                } catch (e) {
                  debugLog(cfg, 'auto-remember-error',
                    { text: mem.text.slice(0, 60), error: String(e && e.message) })
                }
              }
            })
            .catch((e) => debugLog(cfg, 'review-pipeline-error',
              { error: String(e && e.message) }))
        }
      }

      if (!latest) return result

      // ---- 常驻指令：每轮都注入，告诉模型"要主动用记忆工具" ----
      // 这是"记忆读取自动化"的关键：模型要像人一样，需要回忆时自动查、值得记时自动存。
      const memoryPolicy = [
        '### 记忆工具使用准则（重要，请主动遵循）',
        '',
        '你有三个记忆工具，请像使用自己的记忆一样**主动**使用它们，不要被动等待用户下指令：',
        '',
        '1. 回忆（mem_search）：',
        '   当你在对话中**记不清、不确定**用户提到的人、事、约定、偏好、',
        '   编号、密码等具体信息时，**主动调用 mem_search 去回忆**，',
        '   而不是凭印象瞎编，也不是问用户"你还记得吗"。',
        '   这就像你突然想起"哦对，之前聊过这个"。',
        '',
        '2. 记忆（mem_add）：',
        '   当用户透露出**值得长期记住的信息**（偏好、承诺、约定、',
        '   关键事实、重要决定）时，**主动调用 mem_add 存储**，',
        '   而不是等用户说"记住这个"。',
        '   这就像你下意识记住重要的事。',
        '',
        '3. 睡眠整理（mem_sleep）：',
        '   当一个任务/项目**告一段落、完结**，或一轮密集对话结束时，',
        '   你觉得"是时候整理一下这段时间的记忆了"，**主动调用 mem_sleep**。',
        '   系统会帮你合并碎片、强化重要记忆、遗忘过时内容。',
        '   这就像人晚上睡觉时大脑自动巩固白天经历的事。',
        '',
        '判断标准：宁可多查一次、多记一条，也不要漏掉关键信息；',
        '任务完结时主动整理，让记忆保持清晰。',
      ].join('\n')

      // ---- 相关记忆 + 前瞻：只在会话第一次注入 ----
      // 之后每一轮，模型靠"主动 mem_search"自己回忆，系统不再被动塞记忆。
      if (st.injected) {
        // 已注入过：只补常驻指令（相关记忆不再自动塞）
        const contexts = Array.isArray(result.contexts) ? result.contexts : []
        return {
          ...result,
          contexts: [
            ...contexts,
            { name: 'cosmos-mem', text: memoryPolicy },
          ],
        }
      }
      st.injected = true

      // 等待前瞻完成（若还没跑完），超时保护，避免第一次响应卡太久
      if (st.prewarmPromise) {
        try {
          await Promise.race([
            st.prewarmPromise,
            new Promise((resolve) => setTimeout(resolve, 3000)),
          ])
        } catch { /* ignore */ }
      }

      // 计时：分离"检索"与"注入构造"的耗时
      const tSearch0 = Date.now()
      // E-231：多取一些，因为结果里会混入 L0/L1（flag=2）
      const found = await callKernel(cfg, 'search', {
        query: latest, top: cfg.autoInjectTopK ?? 5, scoped: true,
      })
      const tSearch1 = Date.now()
      const allItems = found.results || []
      // 分离 L0/L1（flag=2，受保护）与普通检索结果
      const protectedItems = allItems.filter((it) => it.flag === 2)
      let items = allItems.filter((it) => it.flag !== 2)
      let mode = 'query'
      if (items.length === 0) {
        const slots = await callKernel(cfg, 'search', {
          query: '', top: 3, fixed_slots: 3, min_cost: cfg.minCost ?? 0.7,
        })
        items = (slots.results || []).filter((it) => it.flag !== 2)
        mode = 'slots'
      }
      const tSearch2 = Date.now()

      // E-231：L0/L1 首轮给全文，后续只给标题（省 token 且避免挤占用户记忆）
      const protectedText = formatProtected(protectedItems, !st.injected)

      // E-288 修：普通记忆必须用**真正剩余**的预算。
      //
      // 原代码：
      //   const remainChars = Math.max(300,
      //     (cfg.autoInjectMaxChars ?? 1500) - (st.injected ? 0 : 0))
      //                                              ^^^^^^^^^^^^^^^^^^^
      //   两个三元分支都是 0 —— 等于没扣任何东西！
      //
      // 后果：首轮 L0/L1 全文 2856 字 + 检索结果 1500 字 = 4356 字，
      //       是预算（1500）的 2.9 倍，用户记忆被常驻准则挤爆。
      //
      // 现在：从总预算里扣掉常驻实际占用的字符数，再给检索结果。
      const totalBudget = cfg.autoInjectMaxChars ?? 1500
      const protectedChars = (protectedText || '').length
      const remainChars = Math.max(
        300,                                   // 至少留一点，避免为 0
        totalBudget - protectedChars
      )
      // E-320：低分结果不注入（避免不相关记忆污染上下文）
      //
      // minInjectScore 由配置控制，默认 1.0。
      // 依据：实测「Docker 容器」这类库里无相关内容的查询，
      // 返回的最高分只有 0.96，其余 0.85~0.92，全都不相关。
      // 而真正相关的查询（如「跨版本迁移能力」）最高分 3.05。
      // 1.0 是保守门槛：低于它的多半是"矮子里拔将军"。
      const minScore = typeof cfg.minInjectScore === 'number'
        ? cfg.minInjectScore : 1.0
      const bodyResult = formatMemories(items, remainChars, minScore)
      const body = bodyResult.text
      if (bodyResult.skipped > 0) {
        debugLog(cfg, 'inject-score-filter', {
          kept: bodyResult.kept,
          skipped: bodyResult.skipped,
          minScore,
        })
      }

      // E-263：知识整理提示（仅在会话首次注入，且是机械扫描，无模型调用）
      // 目的：让 note 从"有代码零使用"变成"模型能看到该整理了"
      let knowledgeHint = ''
      if (!st.injected) {
        try {
          const kh = await callKernel(cfg, 'knowledge_hint',
            { min_count: 3, max_items: 4 }, undefined, 8000)
          if (kh && kh.ok && kh.text) knowledgeHint = kh.text
        } catch (e) {
          debugLog(cfg, 'knowledge-hint-fail', { error: String(e && e.message) })
        }
      }

      let prewarmText = ''
      if (st.prewarm && st.prewarm.prediction) {
        prewarmText =
          '### 本轮前瞻（会话开始时的推测）\n' +
          st.prewarm.prediction + '\n' +
          (st.prewarm.topics.length
            ? '可能的话题：' + st.prewarm.topics.join('、') + '\n' : '') +
          (st.prewarm.maybeNeed.length
            ? '可能需要记的：' + st.prewarm.maybeNeed.join('；') + '\n' : '')
      }

      debugLog(cfg, 'assemble', {
        mode, query: latest.slice(0, 60), hits: items.length,
        hasPrewarm: !!prewarmText,
        msSearch: tSearch1 - tSearch0,
        msFallback: tSearch2 - tSearch1,
        msTotal: Date.now() - tSearch0,
      })
      // 记录注入历史（供 UI「注入历史」Tab 展示）
      const injectRec = {
        at: Date.now(),
        sid: sid.slice(0, 20),
        query: latest.slice(0, 120),
        mode,
        items: items.map((it) => ({ ep_id: it.ep_id, text: it.text })),
        protectedCount: protectedItems.length,
        prewarm: prewarmText ? st.prewarm.prediction.slice(0, 200) : '',
      };
      injectHistory.unshift(injectRec)
      if (injectHistory.length > MAX_INJECT_HISTORY) injectHistory.length = MAX_INJECT_HISTORY

      // E-236：注入审计落库（原来只在内存，重启即丢 → 无法事后评估效果）
      // 异步写入，不阻塞本轮响应
      callKernel(cfg, 'inject_log', {
        query: injectRec.query,
        mode,
        sid: injectRec.sid,
        ep_ids: injectRec.items.map((it) => it.ep_id),
        protected_count: protectedItems.length,
        content_chars: (protectedText || '').length + (body || '').length,
        has_prewarm: !!prewarmText,
      }, undefined, 8000).catch(() => { /* 审计失败不影响主流程 */ })

      const contexts = Array.isArray(result.contexts) ? result.contexts : []
      const parts = []
      if (prewarmText) parts.push(prewarmText)
      // E-231：L0/L1 单独一段（首轮全文 / 后续摘要）
      if (protectedText) {
        parts.push('### 常驻准则（L0/L1）\n' +
          (st.injected
            ? '以下是系统长期准则（只显示标题）。需要看某条完整内容时，用 mem_search 查询。\n\n'
            : '以下是系统长期准则，请在后续所有回答中遵循：\n\n') +
          protectedText)
      }
      if (body) {
        parts.push('### 相关记忆\n' +
          '以下是与当前话题相关的过往记忆。回答时优先依据它们；' +
          '若标注了答不出的维度，请如实说明记不清，不要编造。\n\n' + body)
      }
      // E-263：知识整理提示（机械扫描结果，是否整理由模型决定）
      if (knowledgeHint) parts.push(knowledgeHint)

      // E-294：自主记忆准则改为「首轮全文 / 后续一行」
      //
      // 原来无条件每轮注入 877 字（约 585 tokens），占后续轮注入的 40%+。
      // 但这段内容是**固定不变的**——模型看一遍就记住了，
      // 每轮重复发送是纯浪费。
      //
      // 现在：
      //   首轮  → 完整准则（877 字，只发一次）
      //   后续  → 一行提醒（约 30 字）
      //
      // 节省：后续每轮约 847 字 / 565 tokens。
      if (st.injected) {
        parts.push('记忆工具：mem_search / mem_add / mem_sleep 按需主动使用。')
      } else {
        parts.push(memoryPolicy)
      }

      return {
        ...result,
        contexts: [
          ...contexts,
          { name: 'cosmos-mem', text: '## 记忆（cosmos-mem）\n' + parts.join('\n\n') },
        ],
      }
    } catch (err) {
      debugLog(cfg, 'assemble-error', { error: String(err && err.message) })
      return result
    }
  })

  ctx.on('agent/disposed', (payload) => {
    try {
      const sid = payload && payload.agent && payload.agent.id
      if (sid) {
        // E-307：sessions 现在挂在 globalThis 上，用 sessionsMap() 取
        sessionsMap().delete(String(sid))
        scanCursor.delete(String(sid))
      }
    } catch { /* ignore */ }
  })

  // 插件卸载时关闭常驻内核进程，避免留僵尸
  ctx.effect(() => () => {
    try { shutdownKernels() } catch { /* ignore */ }
  })
}
