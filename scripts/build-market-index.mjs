#!/usr/bin/env node
/**
 * build-market-index.mjs — 生成 DSH 插件市场索引（format 1）
 *
 * ============================================================
 * 为什么需要这个
 * ============================================================
 * FISH-17 调查结论（从 DSH asar 源码读出）：
 *   市场 = 一个有 URL 的 JSON 索引文件，用户把它加进 Settings 就能搜到插件。
 *   格式是 format 1，schema 见 @deepseek-ai/dsh-plugin-market-index/src/types.ts。
 *
 * 所以「上架」不需要等官方审核——我们自己产出索引 JSON 就够了。
 * 这个脚本把「发版」和「上架」合成一步：打 tag 后跑一次即可。
 *
 * ============================================================
 * 用法
 * ============================================================
 *   node scripts/build-market-index.mjs
 *   → 读 dsh-plugin/package.json 与 git tag
 *   → 输出 dist/market-index.json
 *
 * ============================================================
 * 关键约束（来自 schema，错了市场会拒收）
 * ============================================================
 * · format 必须是数字 1
 * · name 必须是 npm 包名，且在索引内唯一
 * · versions 非空，按 SemVer **降序**
 * · publishTimestamp 是 Unix 秒（不是毫秒）
 * · source.type='git' 时 commit 必须是**小写 40 位** hex
 * · repository.url 仅展示，绝不是安装源
 */
import { execSync } from 'node:child_process'
import { readFileSync, writeFileSync, mkdirSync, existsSync } from 'node:fs'
import { dirname, join, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

const HERE = dirname(fileURLToPath(import.meta.url))
const ROOT = resolve(HERE, '..')
const PLUGIN_DIR = join(ROOT, 'dsh-plugin')

function sh(cmd, opts = {}) {
  try {
    return execSync(cmd, {
      cwd: opts.cwd || ROOT,
      encoding: 'utf-8',
      stdio: ['ignore', 'pipe', 'ignore'],
    }).trim()
  } catch {
    return ''
  }
}

// ---------------- 1. 读插件清单 ----------------
const pkgPath = join(PLUGIN_DIR, 'package.json')
if (!existsSync(pkgPath)) {
  console.error(`❌ 找不到 ${pkgPath}`)
  process.exit(1)
}
const pkg = JSON.parse(readFileSync(pkgPath, 'utf-8'))

// ---------------- 2. 收集版本 ----------------
//
// 优先用 git tag（真实发版记录）；没有 tag 时退回 package.json 版本。
// 每个版本要能定位到一个确切的 commit —— 这是市场能安装的前提。
const tags = sh('git tag --list "v*" --sort=-v:refname')
  .split('\n')
  .filter(Boolean)

// E-509：一致性检查 —— package.json 版本必须有对应 tag
//
// 问题（实测 2026-11-17）：改名后 package.json 升到 1.1.0，
// 但忘了打 tag，结果索引里最新版本仍是 1.0.0。
// 更糟的是脚本**不报错**，悄悄用旧 tag 生成一个看起来正常的索引，
// 用户从市场装到的是旧版本号。
//
// 现在：如果 package.json 的版本没有对应 tag，直接失败退出，
// 逼着发版的人补上 tag，而不是让错误流到用户手里。
const pkgVer = pkg.version
if (tags.length > 0 && pkgVer) {
  const hasTag = tags.some((t) => t.replace(/^v/, '') === pkgVer)
  if (!hasTag) {
    console.error('')
    console.error('❌ 版本不一致：package.json 是 ' + pkgVer +
      '，但没有对应的 git tag')
    console.error('   现有 tag: ' + tags.slice(0, 5).join(', '))
    console.error('')
    console.error('   修复方式（二选一）：')
    console.error('     git tag -a v' + pkgVer + ' -m "v' + pkgVer + '"')
    console.error('     或把 package.json 版本改回与最新 tag 一致')
    console.error('')
    console.error('   为什么必须拦住：市场索引按 tag 生成，')
    console.error('   不改的话用户会装到旧版本号，而索引看起来是正常的。')
    process.exit(1)
  }
}

const versions = []

if (tags.length > 0) {
  for (const tag of tags) {
    const commit = sh(`git rev-parse "${tag}^{commit}"`)
    if (!/^[0-9a-f]{40}$/.test(commit)) continue
    const ts = sh(`git log -1 --format=%ct "${tag}"`)
    const ver = tag.replace(/^v/, '')
    // 只接受严格 SemVer（市场要求 exact version）
    if (!/^\d+\.\d+\.\d+(-[0-9A-Za-z.-]+)?$/.test(ver)) continue
    versions.push({
      version: ver,
      publishTimestamp: Number(ts) || Math.floor(Date.now() / 1000),
      source: {
        type: 'git',
        url: 'https://github.com/Roxy-DD/fatfish-heart',
        commit,
        path: 'dsh-plugin',
      },
      engines: { dsh: pkg.engines?.node ? undefined : undefined },
    })
  }
}

if (versions.length === 0) {
  // 没有可用 tag：用当前 HEAD 与 package.json 版本兜底
  const commit = sh('git rev-parse HEAD')
  if (!/^[0-9a-f]{40}$/.test(commit)) {
    console.error('❌ 拿不到 HEAD commit，无法生成可安装的索引')
    process.exit(1)
  }
  versions.push({
    version: pkg.version || '0.0.0',
    publishTimestamp: Math.floor(Date.now() / 1000),
    source: {
      type: 'git',
      url: 'https://github.com/Roxy-DD/fatfish-heart',
      commit,
      path: 'dsh-plugin',
    },
    engines: {},
  })
}

// 降序（schema 硬要求）
versions.sort((a, b) => {
  const pa = a.version.split('.').map(Number)
  const pb = b.version.split('.').map(Number)
  for (let i = 0; i < 3; i++) {
    if ((pb[i] || 0) !== (pa[i] || 0)) return (pb[i] || 0) - (pa[i] || 0)
  }
  return 0
})

// ---------------- 3. 组装索引 ----------------
const index = {
  format: 1,
  title: { en: 'Fat Fish Heart', zh: '肥鱼之心' },
  description: {
    en: 'Episodic memory that survives model, provider, and process switches.',
    zh: '跨模型、跨供应商、跨进程都能接着记得的情景记忆系统。',
  },
  metadata: {
    categories: {
      memory: { title: { en: 'Memory', zh: '记忆' } },
    },
    labels: {
      local: { title: { en: 'Local-first', zh: '本地优先' } },
    },
    sortKeys: {},
  },
  plugins: [
    {
      name: pkg.name,
      title: { en: 'Fat Fish Heart', zh: '肥鱼之心' },
      description: {
        en: pkg.description || '',
        zh: '情景记忆：模型自主复盘 + 主动回忆 + 睡眠巩固 + 可跨进程迁移。',
      },
      icon: 'icon.svg',
      author: 'Roxy-DD',
      homepage: 'https://github.com/Roxy-DD/fatfish-heart',
      repository: {
        url: 'https://github.com/Roxy-DD/fatfish-heart',
        directory: 'dsh-plugin',
      },
      metadata: {
        category: 'memory',
        keywords: ['memory', 'episodic', 'sqlite', 'memory-system'],
        labels: ['local'],
        sortKeys: {},
      },
      versions,
    },
  ],
}

// ---------------- 4. 自检（schema 硬约束） ----------------
const errors = []
if (index.format !== 1) errors.push('format 必须是 1')
if (!index.plugins.length) errors.push('plugins 不能为空')
for (const p of index.plugins) {
  if (!p.name) errors.push('plugin.name 缺失')
  if (!p.versions.length) errors.push(`${p.name}: versions 不能为空`)
  for (const v of p.versions) {
    if (!/^\d+\.\d+\.\d+/.test(v.version)) {
      errors.push(`${p.name}: 版本号不是 SemVer: ${v.version}`)
    }
    if (!Number.isInteger(v.publishTimestamp)) {
      errors.push(`${p.name}@${v.version}: publishTimestamp 必须是整数秒`)
    }
    if (v.source.type === 'git') {
      if (!/^[0-9a-f]{40}$/.test(v.source.commit)) {
        errors.push(`${p.name}@${v.version}: git commit 必须是 40 位小写 hex`)
      }
      if (!v.source.url.startsWith('https://')) {
        errors.push(`${p.name}@${v.version}: git url 必须是 https`)
      }
    }
  }
}

if (errors.length) {
  console.error('❌ 索引不合法：')
  for (const e of errors) console.error('   · ' + e)
  process.exit(1)
}

// ---------------- 5. 输出 ----------------
const outDir = join(ROOT, 'dist')
mkdirSync(outDir, { recursive: true })
const outPath = join(outDir, 'market-index.json')
writeFileSync(outPath, JSON.stringify(index, null, 2) + '\n', 'utf-8')

console.log('='.repeat(60))
console.log('市场索引生成完成')
console.log('='.repeat(60))
console.log(`  输出: ${outPath}`)
console.log(`  插件: ${pkg.name}`)
console.log(`  版本: ${versions.length} 个`)
for (const v of versions.slice(0, 5)) {
  console.log(`    ${v.version}  ${v.source.commit.slice(0, 8)}  ${v.publishTimestamp}`)
}
console.log(`
  下一步（部署后用户就能搜到）：
    1. 把这个文件发布到可访问 URL（gh-pages / raw.githubusercontent）
    2. 用户在 DSH 设置里把该 URL 加为市场索引
    3. 搜「肥鱼之心」即可安装

  注意：当前包名是 ${pkg.name}。
  若要让外部用户装，需先改成公开包名（去掉 @local/ 前缀与 private: true）。
`)
