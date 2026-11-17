/**
 * check-offsite-safety.mjs — 异地副本的安全边界（E-519）
 *
 * ============================================================
 * 为什么单独测这个
 * ============================================================
 * proto/offsite.py 是唯一会把**私人记忆**往外网传的代码。
 * 它出错的后果不是"功能不好用"，是"隐私泄露"。
 *
 * 所以这里不测"能不能推"，而是测**安全边界是否牢靠**：
 *   ① 目标仓库是否硬编码（能不能被配置改成别的仓库）
 *   ② 推送前是否真的校验 private（不是相信配置）
 *   ③ 推送前是否校验本地库健康（防止把坏数据固化到远端）
 *   ④ 失败时是否删本地数据（绝不该删）
 *
 * ★ 一个刻意的对抗测试：
 *   如果 offsite.py 里出现 os.environ.get 读取仓库地址、
 *   或者从配置文件读，就说明"硬编码"这条防线被破坏了。
 *   这里静态检查它不读任何外部来源。
 */
import { readFileSync } from 'node:fs'
import { dirname, join, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

const HERE = dirname(fileURLToPath(import.meta.url))
const ROOT = resolve(HERE, '..')
const SRC = readFileSync(join(ROOT, 'proto', 'offsite.py'), 'utf-8')

const results = []
function check(name, cond, detail = '') {
  results.push({ name, ok: !!cond, detail })
  console.log(`  ${cond ? '✅' : '❌'} ${name}${detail ? '  ' + detail : ''}`)
}

console.log('='.repeat(70))
console.log('异地副本安全边界（E-519）')
console.log('='.repeat(70))

// ---------- ① 目标仓库硬编码 ----------
console.log('\n【1】目标仓库必须硬编码（不可被配置改变）')

const repoConst = SRC.match(/ALLOWED_REPO\s*=\s*"([^"]+)"/)
check('ALLOWED_REPO 是字面量常量', !!repoConst,
  repoConst ? repoConst[1] : '未找到')

const urlConst = SRC.match(/ALLOWED_URL\s*=\s*"([^"]+)"/)
check('ALLOWED_URL 是字面量常量', !!urlConst,
  urlConst ? urlConst[1] : '未找到')

// 对抗测试：不能从环境变量/配置读仓库地址
const repoFromEnv = /ALLOWED_REPO\s*=\s*os\.environ/.test(SRC)
const repoFromFile = /ALLOWED_REPO\s*=\s*(?:json\.load|open\()/.test(SRC)
check('ALLOWED_REPO 不从环境变量读（防篡改）', !repoFromEnv)
check('ALLOWED_REPO 不从配置文件读（防篡改）', !repoFromFile)

// 仓库名必须是私有的那个，不能是公开的 fatfish-heart
if (repoConst) {
  check('目标不是公开仓库 fatfish-heart',
    repoConst[1] !== 'Roxy-DD/fatfish-heart',
    repoConst[1])
  check('目标是专用记忆仓库 fatfish-heart-memory',
    repoConst[1].endsWith('fatfish-heart-memory'), repoConst[1])
}

// ---------- ② 推送前校验 private ----------
console.log('\n【2】推送前实时校验 private')

check('有 check_repo_private 函数',
  /def check_repo_private/.test(SRC))
check('用 gh api 实时查询（不是读配置）',
  /gh",\s*"api"/.test(SRC) || /"gh",\s*"api"/.test(SRC))
check('校验 .private 字段',
  /\.private/.test(SRC))
check('非 private 时拒绝推送',
  /不是 private！拒绝推送/.test(SRC) || /拒绝推送/.test(SRC))

// cmd_push 必须在最前面调用这个检查
const pushFn = SRC.slice(SRC.indexOf('def cmd_push'),
  SRC.indexOf('def cmd_verify'))
check('cmd_push 里调用了 check_repo_private',
  /check_repo_private\(\)/.test(pushFn))
const checkCallIdx = pushFn.indexOf('check_repo_private()')
const snapshotIdx = pushFn.indexOf('src.backup(dst)')
check('安全校验在快照之前（先验再动）',
  checkCallIdx >= 0 && snapshotIdx > checkCallIdx,
  `check@${checkCallIdx} snapshot@${snapshotIdx}`)

// ---------- ③ 推送前校验本地库健康 ----------
console.log('\n【3】推送前校验本地库健康')

check('有 PRAGMA integrity_check', /integrity_check/.test(SRC))
check('检查 episodes 数量', /FROM episodes/.test(SRC))
check('integrity 不为 ok 时拒绝',
  /完整性检查失败/.test(SRC) || /integrity.*!=.*"ok"/.test(SRC))
check('episodes 为 0 时拒绝（防空库覆盖远端）',
  /episodes 为 0/.test(SRC))

// ---------- ④ 失败不删本地 ----------
console.log('\n【4】失败不删本地数据')

// 检查有没有在 push 路径上删 DB 的代码
const dangerPatterns = [
  /os\.remove\(DB_PATH\)/,
  /os\.unlink\(DB_PATH\)/,
  /shutil\.rmtree\(DB_PATH/,
]
const found = dangerPatterns.filter((p) => p.test(SRC))
check('没有删除 DB_PATH 的代码', found.length === 0,
  found.length ? `发现 ${found.length} 处` : '')

// rmtree 只应该用在临时目录
const rmtrees = SRC.match(/shutil\.rmtree\(([^,)]+)/g) || []
const badRmtree = rmtrees.filter((m) => !/tmpdir|tempdir|tmp/i.test(m))
check('rmtree 只用于临时目录', badRmtree.length === 0,
  badRmtree.join(', ') || '')

// ---------- ⑤ WAL 安全（不能用 copy） ----------
console.log('\n【5】WAL 模式下必须用 backup API')

check('用 sqlite backup 而非 shutil.copy',
  /src\.backup\(dst\)/.test(SRC))
// 允许有 fallback，但要标注警告
if (/shutil\.copy2\(DB_PATH/.test(SRC)) {
  check('copy 只作为 fallback 且有警告',
    /快照失败，改用文件复制/.test(SRC))
} else {
  check('没有直接 copy 主库', true)
}

// ---------- ⑥ 远端保留策略 ----------
console.log('\n【6】远端保留策略（防无限增长）')

check('有 KEEP_REMOTE 常量', /KEEP_REMOTE\s*=\s*\d+/.test(SRC))
check('会删除超出数量的旧备份', SRC.includes('while len(old_dbs) > KEEP_REMOTE'))
// ★ 关键：必须用 git add -A，否则删除不会被 stage，旧文件仍在仓库里
check('用 git add -A（能 stage 删除）',
  /"add",\s*"-A"/.test(SRC))
check('没有用 add <单个文件>（那样不跟踪删除）',
  !/"add",\s*name,/.test(SRC))

// 文件名格式必须是定长时间戳，否则字典序 != 时间序
check('文件名用定长时间戳（字典序=时间序）',
  SRC.includes('%Y%m%d_%H%M%S'))

// ---------- 汇总 ----------
const pass = results.filter((x) => x.ok).length
console.log('\n' + '='.repeat(70))
console.log(`结果：${pass}/${results.length} 通过`)
for (const x of results.filter((y) => !y.ok)) {
  console.log(`  ❌ ${x.name}  ${x.detail}`)
}
console.log('='.repeat(70))
console.log(`
★ 这个测试的对象是隐私，不是功能：
  offsite.py 是唯一把私人记忆传到外网的代码。
  它出错的后果不是"不好用"，是"泄露"。

  所以这里用对抗性检查：不看它有没有实现功能，
  而是看它的防线能不能被绕过（改配置、读环境变量、空库覆盖）。
`)

process.exit(pass === results.length ? 0 : 1)
