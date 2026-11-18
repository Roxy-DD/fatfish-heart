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
// ★ 关键：必须能 stage 新文件**和**删除
//   E-523 踩坑：改用 sparse-checkout 后，`git add -A` 不会加入
//   被稀疏排除的文件 → commit 里没有新备份，但 push 仍报成功。
//   现在用 `git add --sparse`，两条 add 都带 --sparse。
check('add 带 --sparse（否则新文件进不了 commit）',
  SRC.includes('"--sparse"'))
check('能 stage 删除（-A 与 --sparse 同时使用）',
  SRC.includes('"--sparse", "-A"') || /"add",\s*"--sparse",\s*"-A"/.test(SRC))
check('没有用裸 add <单个文件>（那样不跟踪删除）',
  !/"add",\s*name,/.test(SRC))

// 文件名格式必须是定长时间戳，否则字典序 != 时间序
check('文件名用定长时间戳（字典序=时间序）',
  SRC.includes('%Y%m%d_%H%M%S'))

// ---------- ⑦ restore 的安全设计 ----------
console.log('\n【7】restore 必须防「恢复反而弄丢数据」')

const restoreFn = SRC.slice(SRC.indexOf('def cmd_restore'),
  SRC.indexOf('def main'))

check('实现存在', restoreFn.length > 100)
check('本地库存在时要求 --force', SRC.includes('--force'))
check('覆盖前备份当前库', restoreFn.includes('before-restore'))
check('覆盖前校验指纹', restoreFn.includes('指纹不符'))
check('覆盖前校验 integrity_check', restoreFn.includes('integrity_check'))
check('覆盖前拒绝空库', restoreFn.includes('空库'))
// ★ 关键：WAL 模式下必须清 -wal/-shm，否则恢复后被旧 WAL 覆盖
check('覆盖时清理 -wal/-shm', restoreFn.includes('"-wal"') &&
  restoreFn.includes('"-shm"'))

// ---------- ⑧ 分支 ref 必须显式指定 ----------
console.log('\n【8】API 必须显式指定分支（否则读到默认分支的旧数据）')

// E-521 根治后：分支不再是常量，而是运行时解析默认分支
check('有 branch() 运行时解析函数',
  /def branch\(\)/.test(SRC) || SRC.includes('def branch():'))
check('branch() 会问 GitHub 的 default_branch',
  SRC.includes('.default_branch'))
check('branch() 有回退值（拿不到时不崩）',
  SRC.includes('name = "master"') || SRC.includes("name = 'master'"))

// ★ 这个 bug 极其隐蔽：
//   不指定 ref 时 GitHub 读**默认分支**。若默认分支是 main
//   而数据推到 master，push 报成功、verify 也能过，
//   但验证的永远是上一次的备份。
// 只检查**真实调用**（gh api 那一行），不看注释里的示例。
// 第一版把注释 L369 的 `contents/<name>` 示例也算了进去，报了假失败。
const codeLines = SRC.split('\n').filter((l) => {
  const t = l.trim()
  return !t.startsWith('#') && !t.startsWith('//') && t.includes('contents')
})
const withoutRef = codeLines.filter((l) => !l.includes("ref={branch()}"))
check('所有 contents API 都带 ref', withoutRef.length === 0,
  withoutRef.length ? `裸调用: ${withoutRef[0].trim().slice(0, 60)}` : '')

// ---------- ⑨ 清单必须以实际文件为准 ----------
console.log('\n【9】MANIFEST 必须与实际文件一致')

const pushFn2 = SRC.slice(SRC.indexOf('def cmd_push'),
  SRC.indexOf('def cmd_verify'))

check('清单扫描实际文件（不是盲目 append）',
  pushFn2.includes('actual_files'))
check('有清单补录逻辑（文件在但没记录时）',
  pushFn2.includes('清单补录'))
check('会丢弃指向已删文件的记录',
  pushFn2.includes('dropped') || pushFn2.includes('丢弃已删除'))
check('没有单纯 append 的旧写法',
  !pushFn2.includes('manifest["backups"].append'))

// ---------- ⑩ push 后必须确认真到了远端 ----------
console.log('\n【10】push 后必须确认真到了远端')

check('push 末尾有远端确认', SRC.includes('远端已确认'))
check('大小不符时返回非 0', SRC.includes('推送后校验失败'))
// 存在原因：sparse-checkout 下 git add -A 不加入被排除的文件，
// commit 里没有新备份但 push 仍报成功。只有直接问远端才能发现。

// ---------- ⑪ restore 后必须回读确认 ----------
console.log('\n【11】restore 后必须回读确认（不能只凭 copy 没抛异常）')

check('恢复了 expected_episodes 供比对', SRC.includes('expected_episodes'))
check('回读确认输出', SRC.includes('已恢复并回读确认'))
check('条数不符时报错', SRC.includes('恢复后条数不符'))
check('完整性失败时报错', SRC.includes('恢复后完整性检查失败'))

// ---------- ⑫ prune 不能静默失效 ----------
console.log('\n【12】prune 不能静默失效（E-526）')

check('删之前先建立索引', SRC.includes('checkout", "-q", "HEAD", "--", "."') || SRC.includes('HEAD", "--", "."'))
check('索引为空时警告', SRC.includes('git 索引为空'))
check('rm 失败时打印错误', SRC.includes('无法从索引移除'))
// ★ 清理类操作不能用 except: pass 吞掉 —— 它保证主流程不中断，
//   也保证你永远不知道清理没生效（E-526 就是这样漏了 3 个月）
const pruneBlock = SRC.slice(SRC.indexOf('while len(old_dbs) > KEEP_REMOTE'),
  SRC.indexOf('清理旧备份'))
// 区分两种 except: pass：
//   · 删工作区文件失败 → 可接受（partial clone 下本来就没有）
//   · git rm 失败但继续 → 不可接受（那样 prune 就静默失效了）
check('git rm 失败时会报错而不是继续',
  SRC.includes('无法从索引移除'))
check('previously-silent 的 os.remove 现在有 git rm 兜底',
  pruneBlock.includes('git", "rm", "--cached"'))

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
