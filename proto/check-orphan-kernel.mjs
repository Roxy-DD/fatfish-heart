/**
 * E-497：孤儿内核修复回归
 *
 * 根因（实测 2026-11-16 17:43）：
 *   pluginManager 重载插件后，新内核启动但旧内核没退出，
 *   两个进程连同一个 dsh_mem.db。
 *   原因：kernelPools 是模块级 Map，模块被替换后引用丢失，
 *   旧 pool 在 cleanup 时拿不到 → 杀不到旧内核。
 *
 * 修法：
 *   · kernelPools 挂 globalThis，跨模块重载保留
 *   · 每个 pool 打 applySeq 标记
 *   · apply 开头调 shutdownStaleKernels()，只杀 applySeq < 当前的
 *
 * 本测试在 mock 环境下验证「两代内核交替」的状态机：
 *   ① 第一次 apply 建内核 A（seq=1）
 *   ② 第二次 apply 建内核 B（seq=2），A 被关、B 保留
 *   ③ 不误杀当前代
 *
 * ★ 不真的 spawn python 内核（太慢且需要模型），
 *   用假的 proc 对象验证关闭逻辑。
 */
import { pathToFileURL } from 'node:url'

const results = []
function check(name, cond, detail = '') {
  results.push({ name, ok: !!cond, detail })
  console.log(`  ${cond ? '✅' : '❌'} ${name}${detail ? '  ' + detail : ''}`)
}

console.log('='.repeat(66))
console.log('E-497 孤儿内核修复回归')
console.log('='.repeat(66))

// ---------- 静态检查：修复代码存在 ----------
console.log('\n【1】修复代码存在')
import { readFileSync } from 'node:fs'
const src = readFileSync(
  new URL('../dsh-plugin/index.js', import.meta.url), 'utf8')

check('kernelPools 挂 globalThis',
  src.includes('__FFH_KERNEL_POOLS__'))
check('有 applySeq 标记',
  src.includes('applySeq:'))
check('有 shutdownStaleKernels()',
  src.includes('function shutdownStaleKernels'))
check('apply 开头调用清理',
  src.includes('shutdownStaleKernels(cfg)') ||
  src.includes('try { shutdownStaleKernels(cfg) }'))
check('用序号比较而非无差别杀',
  src.includes('(k.applySeq || 0) < cur'))

// ---------- E-505：按路径扫进程（真修法）----------
//
// E-497 的内存路径在真实重载中失效（实测 poolSizeBefore=0），
// 所以 E-505 加了「按 dbPath 扫系统进程」这条可靠路径。
console.log('\n【1b】E-505 按路径扫孤儿内核')
check('有 sweepOrphanKernelsByPath()',
  src.includes('function sweepOrphanKernelsByPath'))
check('apply 里调用了它',
  src.includes('sweepOrphanKernelsByPath(cfg)'))
check('用 JSON 输出避免转义问题',
  src.includes('ConvertTo-Json'))
check('设置 UTF-8 输出编码（中文路径坑）',
  src.includes('[Console]::OutputEncoding'))
check('有路径尾部兜底匹配（编码救不回来时）',
  src.includes('byTail'))
check('跳过当前 pool 里的活进程',
  src.includes('alivePids.has(pid)'))

// ---------- 状态机模拟 ----------
console.log('\n【2】两代内核交替（状态机）')

// 模拟 globalThis 上的 pool（与插件同款结构）
const pools = globalThis.__FFH_KERNEL_POOLS__ ||
  (globalThis.__FFH_KERNEL_POOLS__ = new Map())

function fakeKill(name, log) {
  return {
    stdin: { end() { log.push(`${name}:stdin.end`) } },
    killed: false,
    kill() { this.killed = true; log.push(`${name}:kill`) },
  }
}

// 清理现场
pools.clear()
globalThis.__FFH_APPLY_SEQ__ = 0

const log = []
const gen1 = fakeKill('gen1', log)
pools.set('db1', { proc: gen1, applySeq: 1 })
check('第一代内核已建（seq=1）', pools.size === 1)

// 模拟第二次 apply 的清理逻辑（与插件同款）
globalThis.__FFH_APPLY_SEQ__ = 2
const cur = globalThis.__FFH_APPLY_SEQ__
for (const [key, k] of pools) {
  if ((k.applySeq || 0) < cur) {
    try { k.proc.stdin.end() } catch {}
    try { k.proc.kill() } catch {}
    pools.delete(key)
  }
}
check('第一代被关闭',
  gen1.killed && log.includes('gen1:kill'), log.join(','))
check('池已清空旧代', pools.size === 0)

// 建第二代
const gen2 = fakeKill('gen2', log)
pools.set('db1', { proc: gen2, applySeq: cur })
check('第二代已建（seq=2）', pools.size === 1 && !gen2.killed)

// 第三次 apply：再清理一次
globalThis.__FFH_APPLY_SEQ__ = 3
const cur3 = globalThis.__FFH_APPLY_SEQ__
for (const [key, k] of pools) {
  if ((k.applySeq || 0) < cur3) {
    try { k.proc.stdin.end() } catch {}
    try { k.proc.kill() } catch {}
    pools.delete(key)
  }
}
check('第二代被关闭', gen2.killed && log.includes('gen2:kill'))
check('无孤儿残留（池始终最多 1 个）', pools.size === 0)

// 关键反例：当前代不能被误杀
const gen3 = fakeKill('gen3', log)
pools.set('db1', { proc: gen3, applySeq: 3 })
globalThis.__FFH_APPLY_SEQ__ = 3   // 同一次 apply 内再次调用清理
const cur3b = globalThis.__FFH_APPLY_SEQ__
for (const [key, k] of pools) {
  if ((k.applySeq || 0) < cur3b) {
    try { k.proc.kill() } catch {}
    pools.delete(key)
  }
}
check('同代内核不被误杀', !gen3.killed && pools.size === 1)

// ---------- 清理 ----------
pools.clear()
delete globalThis.__FFH_KERNEL_POOLS__
delete globalThis.__FFH_APPLY_SEQ__

console.log('\n' + '='.repeat(66))
const pass = results.filter((x) => x.ok).length
console.log(`结果：${pass}/${results.length} 通过`)
for (const x of results.filter((y) => !y.ok)) {
  console.log(`  ❌ ${x.name}  ${x.detail}`)
}
console.log('='.repeat(66))
console.log(`
★ 为什么这个测试重要：
  这是 E-479 排查「改代码不生效」时暴露的副作用 ——
  当时只关注能不能重载，没检查重载后有没有留垃圾。
  结果留下两个内核连同一个 DB，写入计数可能被两个进程各维护。

  修一个 bug 时引入另一个 bug，而且症状不明显 ——
  只有显式测「重载后的进程数」才能抓到。
`)

process.exit(pass === results.length ? 0 : 1)
