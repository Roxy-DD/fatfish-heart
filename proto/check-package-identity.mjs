/**
 * check-package-identity.mjs — 包名一致性（E-515）
 *
 * ============================================================
 * 为什么需要这个测试
 * ============================================================
 * 2026-11-17 改名时我改了服务端（index.js / package.json / patch.yml），
 * 但漏了 client.js 里的 `window.__ModuleLoader__.load({ id: ... })`。
 *
 * 后果比预期严重：
 *   · DSH 按新包名找客户端模块 → 找不到 → 整个 UI 不加载
 *   · 但服务端 HTTP 仍然正常（/cosmos-mem/stats 返回 200）
 *   · 症状是「插件好像还在，但记忆不注入了」，比直接报错更难查
 *
 * 而且**没有任何现成测试覆盖这个 ID**，所以遗漏没被发现。
 *
 * ============================================================
 * 本测试检查什么
 * ============================================================
 * 包名在 4 个地方出现，必须完全一致：
 *   ① package.json          name
 *   ② cordis.patch.yml      insert[0].name
 *   ③ client.js             __ModuleLoader__.load({ id: })
 *   ④ profile/package.json  dependencies 的 key（本机安装时）
 *
 * 允许不一致的（故意的，不改）：
 *   · index.js 的 `export const name = 'cosmos-mem'`（内部显示名）
 *   · HTTP 路由前缀 `/cosmos-mem`（已装用户的 URL 不能变）
 *   · Tab 的 id/key（前端内部标识）
 */
import { readFileSync, existsSync } from 'node:fs'
import { dirname, join, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import { homedir } from 'node:os'

const HERE = dirname(fileURLToPath(import.meta.url))
const ROOT = resolve(HERE, '..')
const PLUGIN = join(ROOT, 'dsh-plugin')

const results = []
function check(name, cond, detail = '') {
  results.push({ name, ok: !!cond, detail })
  console.log(`  ${cond ? '✅' : '❌'} ${name}${detail ? '  ' + detail : ''}`)
}

console.log('='.repeat(70))
console.log('包名一致性（E-515）')
console.log('='.repeat(70))

// ---------- ① package.json ----------
const pkg = JSON.parse(readFileSync(join(PLUGIN, 'package.json'), 'utf-8'))
const pkgName = pkg.name
console.log(`\n【基准】package.json name = ${pkgName}`)
check('包名不是 @local/ 作用域（否则外部装不到）',
  !pkgName.startsWith('@local/'), pkgName)
check('没有 private:true（否则禁止发布）',
  pkg.private !== true)

// ---------- ② cordis.patch.yml ----------
console.log('\n【②】cordis.patch.yml')
const patch = readFileSync(join(PLUGIN, 'cordis.patch.yml'), 'utf-8')
const nameMatch = patch.match(/name:\s*'([^']+)'/)
check('patch.yml 有 name 字段', !!nameMatch, nameMatch ? nameMatch[1] : '缺失')
if (nameMatch) {
  check('patch.yml name 与 package.json 一致',
    nameMatch[1] === pkgName, `${nameMatch[1]} vs ${pkgName}`)
}

// ---------- ③ client.js 的 ModuleLoader id ----------
console.log('\n【③】client.js __ModuleLoader__ id（上次漏的就是这里）')
const client = readFileSync(join(PLUGIN, 'client.js'), 'utf-8')
// ★ 必须先剥掉注释再匹配
//   第一版直接正则匹配，结果匹配到注释里的示例 `@local/my-decoration`，
//   报了一个假失败。注释里出现的示例不是真实代码。
const clientCode = client
  .replace(/\/\*[\s\S]*?\*\//g, '')     // 块注释
  .replace(/^\s*\/\/.*$/gm, '')         // 行注释
const idMatch = clientCode.match(
  /__ModuleLoader__\.load\(\{\s*[\s\S]{0,200}?id:\s*'([^']+)'/)
check('client.js 有 ModuleLoader.load({ id })',
  !!idMatch, idMatch ? idMatch[1] : '未找到（结构可能变了）')
if (idMatch) {
  check('client.js id 与 package.json 一致',
    idMatch[1] === pkgName, `${idMatch[1]} vs ${pkgName}`)
}

// ---------- ④ 本机 profile（存在才查） ----------
console.log('\n【④】本机已装 profile')
const profPath = join(homedir(), '.dsh', 'profiles', 'desktop', 'package.json')
if (existsSync(profPath)) {
  try {
    const prof = JSON.parse(readFileSync(profPath, 'utf-8'))
    const deps = Object.keys(prof.dependencies || {})
      .filter((k) => k.includes('fatfish') || k.includes('cosmos'))
    const bundles = (prof.dsh?.profile?.bundles || [])
      .filter((b) => b.includes('fatfish') || b.includes('cosmos'))

    check('profile 里只有一个相关依赖（没有新旧并存）',
      deps.length === 1, deps.join(', ') || '(空)')
    if (deps.length === 1) {
      check('profile 依赖名与 package.json 一致',
        deps[0] === pkgName, `${deps[0]} vs ${pkgName}`)
    }
    check('profile bundles 与 package.json 一致',
      bundles.length === 1 && bundles[0] === pkgName,
      bundles.join(', ') || '(空)')
  } catch (e) {
    check('读取 profile', false, String(e.message).slice(0, 80))
  }
} else {
  console.log('  （本机无 desktop profile，跳过 —— 这在 CI 里是正常的）')
}

// ---------- 允许不一致的地方（防止过度修改） ----------
console.log('\n【附】故意不改的地方（写下来防止下次又去改）')
const indexSrc = readFileSync(join(PLUGIN, 'index.js'), 'utf-8')
check('index.js 的 export name 保持 cosmos-mem（内部显示名）',
  /export const name = 'cosmos-mem'/.test(indexSrc))
check('HTTP 路由前缀保持 /cosmos-mem（已装用户的 URL 不能变）',
  /httpBase: '\/cosmos-mem'/.test(indexSrc))
check('client.js 里 BASE 保持 /cosmos-mem',
  /BASE = '\/cosmos-mem'/.test(client))

// ---------- 汇总 ----------
const pass = results.filter((x) => x.ok).length
console.log('\n' + '='.repeat(70))
console.log(`结果：${pass}/${results.length} 通过`)
for (const x of results.filter((y) => !y.ok)) {
  console.log(`  ❌ ${x.name}  ${x.detail}`)
}
console.log('='.repeat(70))
console.log(`
★ 这个测试的由来：
  改名时改了 3 个地方，漏了第 4 个（client.js 的 ModuleLoader id）。
  服务端正常、UI 挂了，症状隐蔽。而且没有测试覆盖，所以没人发现。

  现在包名出现在 5 个地方，任何一个不一致都会让插件半死。
`)

process.exit(pass === results.length ? 0 : 1)
