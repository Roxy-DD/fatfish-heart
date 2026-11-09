/**
 * check-scan-cursor.mjs —— recentUserMessages 的常驻回归测试
 *
 * 覆盖 E-324/329 修的两个 bug：
 *   ① cursor 语义错误（记末尾 seq 而非最后采纳消息的 seq）
 *   ② log 重置后 cursor 失效（seq 重新从小数开始，全被跳过）
 *
 * 这是"静默失败"类 bug：recentCount 一直是 0，
 * 但没有任何报错，只是检索一直用旧 query。
 */

// 复刻 index.js 里的实现（保持同步）
const scanCursor = new Map()

function recentUserMessages(log, n, sid) {
  if (!Array.isArray(log) || n <= 0) return []
  let last = sid ? scanCursor.get(sid) : undefined

  // log 重置检测
  if (typeof last === 'number' && last > 0 && log.length > 0) {
    let maxSeq = 0
    for (let i = log.length - 1; i >= Math.max(0, log.length - 5); i--) {
      const e = log[i]
      if (e && typeof e.seq === 'number' && e.seq > maxSeq) maxSeq = e.seq
    }
    if (maxSeq > 0 && maxSeq < last) {
      scanCursor.delete(sid)
      last = undefined
    }
  }

  const out = []
  for (let i = log.length - 1; i >= 0 && out.length < n; i--) {
    const e = log[i]
    if (!e || e.type !== 'user/message') continue
    if (typeof last === 'number' && typeof e.seq === 'number' &&
        e.seq <= last) continue
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
    if (text.startsWith('Current runtime context')) continue
    if (text.startsWith('background job')) continue
    if (text) out.push({ seq: e.seq, text })
  }

  if (sid && out.length > 0) {
    const maxSeq = Math.max(...out.map((x) =>
      typeof x.seq === 'number' ? x.seq : 0))
    if (maxSeq > 0) {
      const prev = scanCursor.get(sid)
      if (typeof prev !== 'number' || maxSeq > prev) {
        scanCursor.set(sid, maxSeq)
      }
    }
  }

  return out.reverse()
}

let pass = 0
let fail = 0
const problems = []

function check(name, cond, detail = '') {
  if (cond) {
    pass++
    console.log(`  ✅ ${name}${detail ? '  ' + detail : ''}`)
  } else {
    fail++
    problems.push(`${name}  ${detail}`)
    console.log(`  ❌ ${name}${detail ? '  ' + detail : ''}`)
  }
}

function mkLog(texts, startSeq = 1) {
  const log = []
  let s = startSeq
  for (const t of texts) {
    log.push({
      seq: s++, type: 'user/message',
      data: { source: { kind: 'user' },
              content: [{ type: 'text', text: t }] },
    })
    log.push({ seq: s++, type: 'assistant/message', data: {} })
  }
  return log
}

console.log('='.repeat(66))
console.log('scan-cursor 回归测试')
console.log('='.repeat(66))

// ---------- ① 基本提取 ----------
console.log('\n【1】基本提取')
{
  scanCursor.clear()
  const log = mkLog(['消息A', '消息B', '消息C'])
  const r = recentUserMessages(log, 8, 's1')
  check('提取到 3 条', r.length === 3, String(r.length))
  check('顺序从旧到新', r[0].text === '消息A' && r[2].text === '消息C')
  check('cursor 记录最大 seq', scanCursor.get('s1') === 5,
    String(scanCursor.get('s1')))
}

// ---------- ② 增量：不重复提取 ----------
console.log('\n【2】增量不重复')
{
  scanCursor.clear()
  const log = mkLog(['旧1', '旧2'])
  recentUserMessages(log, 8, 's2')
  const r2 = recentUserMessages(log, 8, 's2')
  check('重复调用返回空', r2.length === 0, String(r2.length))
}

// ---------- ③ 新消息能被提取 ----------
console.log('\n【3】新消息能被提取')
{
  scanCursor.clear()
  const log = mkLog(['旧1'])
  recentUserMessages(log, 8, 's3')
  // 加新消息
  log.push({
    seq: 10, type: 'user/message',
    data: { source: { kind: 'user' },
            content: [{ type: 'text', text: '新消息' }] },
  })
  const r = recentUserMessages(log, 8, 's3')
  check('提取到新消息', r.length === 1 && r[0].text === '新消息',
    JSON.stringify(r))
}

// ---------- ④ log 重置后仍能提取（关键）----------
console.log('\n【4】log 重置后仍能提取（E-329 核心）')
{
  scanCursor.clear()
  // 先处理一个大 seq 的 log
  const oldLog = mkLog(['旧1', '旧2', '旧3'], 27000)
  recentUserMessages(oldLog, 8, 's4')
  check('旧 log 处理后 cursor 很大',
    (scanCursor.get('s4') || 0) > 27000,
    String(scanCursor.get('s4')))

  // log 被重置，seq 从 1 重新开始
  const newLog = mkLog(['重置后1', '重置后2'], 1)
  const r = recentUserMessages(newLog, 8, 's4')
  check('重置后能提取到消息', r.length === 2, String(r.length))
  check('提取的是新内容',
    r.length === 2 && r[0].text === '重置后1' && r[1].text === '重置后2',
    r.map((x) => x.text).join(','))
  check('cursor 已重置为小值',
    (scanCursor.get('s4') || 0) < 100,
    String(scanCursor.get('s4')))
}

// ---------- ⑤ 排除系统消息 ----------
console.log('\n【5】排除系统注入的消息')
{
  scanCursor.clear()
  const log = [
    { seq: 1, type: 'user/message',
      data: { source: { kind: 'user' },
              content: [{ type: 'text', text: '真实用户消息' }] } },
    { seq: 2, type: 'user/message',
      data: { source: { kind: 'time-context' },
              content: [{ type: 'text', text: 'Time sampled while preparing turn 1' }] } },
    { seq: 3, type: 'user/message',
      data: { source: { kind: 'cosmos-mem-lifecycle' },
              content: [{ type: 'text', text: '### 继续自主迭代' }] } },
    { seq: 4, type: 'user/message',
      data: { source: { kind: 'user' },
              content: [{ type: 'text', text: 'Current runtime context. xxx' }] } },
  ]
  const r = recentUserMessages(log, 8, 's5')
  check('只提取真实用户消息', r.length === 1, String(r.length))
  check('内容是真实消息', r.length === 1 && r[0].text === '真实用户消息',
    r.map((x) => x.text).join(','))
}

// ---------- ⑥ reviewWindow 限制 ----------
console.log('\n【6】reviewWindow 限制生效')
{
  scanCursor.clear()
  const log = mkLog(['a', 'b', 'c', 'd', 'e'])
  const r = recentUserMessages(log, 3, 's6')
  check('最多返回 n 条', r.length === 3, String(r.length))
  check('返回的是最后 3 条',
    r.map((x) => x.text).join(',') === 'c,d,e',
    r.map((x) => x.text).join(','))
}

// ---------- 汇总 ----------
console.log('\n' + '='.repeat(66))
console.log(`结果：${pass}/${pass + fail} 通过`)
if (fail > 0) {
  console.log('\n失败项：')
  problems.forEach((p, i) => console.log(`  ${i + 1}. ${p}`))
}
console.log('='.repeat(66))

process.exit(fail === 0 ? 0 : 1)
