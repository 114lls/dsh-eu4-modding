/** 冒烟测试：打包结构与技能注册契约。
 *
 * 跑法：node --test tests/smoke.test.mjs
 */
import assert from 'node:assert/strict'
import { existsSync, readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { test } from 'node:test'
import { fileURLToPath, pathToFileURL } from 'node:url'

const HERE = dirname(fileURLToPath(import.meta.url))
const ROOT = join(HERE, '..')

const read = (p) => readFileSync(join(ROOT, p), 'utf8')
const json = (p) => JSON.parse(read(p))
// ★ Windows 上动态 import 绝对路径必须转成 file:// URL，
//   否则报 ERR_UNSUPPORTED_ESM_URL_SCHEME（protocol 'c:'）。
//   用 pathToFileURL 而不是手拼字符串，才能正确处理空格与中文路径。
const importLib = async () => import(pathToFileURL(join(ROOT, 'lib', 'index.js')).href)

test('package.json 关键字段齐全', () => {
  const pkg = json('package.json')
  assert.equal(pkg.name, 'dsh-eu4-modding')
  assert.equal(pkg.main, 'lib/index.js')
  assert.equal(pkg.dsh.bundle.patch, './cordis.patch.yml')
  assert.ok(pkg.peerDependencies['@deepseek-ai/cordis'])
  assert.ok(pkg.peerDependencies['@deepseek-ai/dsh-skill'])
  assert.equal(pkg.license, 'MIT')
})

test('打包 files 白名单排除 reference/ 与产物', () => {
  const pkg = json('package.json')
  const files = pkg.files
  // ★ reference/ 是 vanilla 衍生数据，绝不能进包。
  //   注意不能只用 includes('reference')：`references/*.md` 也含这个子串，
  //   会误判成违规。要按路径段精确判断。
  const isReferenceDir = (f) =>
    f === 'reference' || f.startsWith('reference/') || f.startsWith('reference\\')
  assert.ok(!files.some(isReferenceDir), 'reference/ 出现在 files 里')
  assert.ok(!files.some((f) => f.includes('__pycache__')))
  assert.ok(!files.some((f) => f.includes('*.pyc')))
  assert.ok(!files.some((f) => f.startsWith('tests/')), '测试不该进包')
  // 技能本体必须在包里
  assert.ok(files.includes('skills/eu4-modding/SKILL.md'))
  assert.ok(files.some((f) => f.includes('skills/eu4-modding/scripts/')))
  // references（技能自带的文档）应该在包里，与 reference（生成物）不同
  assert.ok(files.some((f) => f.includes('skills/eu4-modding/references/')),
            '技能自带 references 文档应进包')
})

test('.gitignore 排除 reference/ 与 pycache', () => {
  const gi = read('.gitignore')
  assert.match(gi, /^reference\//m)
  assert.match(gi, /^__pycache__\//m)
  assert.match(gi, /\*\.pyc/)
  assert.match(gi, /tmp_\*\.py/)
})

test('cordis.patch.yml 结构正确', () => {
  const yml = read('cordis.patch.yml')
  assert.match(yml, /- insert:/)
  assert.match(yml, /id: eu4-modding/)
  assert.match(yml, /name: dsh-eu4-modding/)
})

test('SKILL.md frontmatter 可解析且字段齐全', () => {
  const skill = read('skills/eu4-modding/SKILL.md')
  const m = /^---\r?\n([\s\S]*?)\r?\n---/.exec(skill)
  assert.ok(m, 'SKILL.md 缺 frontmatter')
  const fm = m[1]
  for (const key of ['name:', 'description:', 'whenToUse:']) {
    assert.ok(fm.includes(key), `frontmatter 缺 ${key}`)
  }
  assert.match(fm, /name: eu4-modding/)
})

test('lib/index.js 导出注册契约', async () => {
  const mod = await importLib()
  assert.equal(mod.name, 'eu4-modding-skill')
  assert.deepEqual(mod.inject, ['skills'])
  assert.equal(typeof mod.apply, 'function')
  assert.equal(typeof mod.loadSkill, 'function')
  assert.equal(typeof mod.parseFrontmatter, 'function')

  const skill = mod.loadSkill()
  assert.equal(skill.name, 'eu4-modding')
  assert.equal(skill.provider, 'eu4-modding')
  assert.equal(skill.source, 'bundled')
  assert.ok(skill.description.length > 40)
  assert.ok(skill.whenToUse.length > 10)
  assert.equal(skill.resourceBase.kind, 'directory')
  assert.ok(existsSync(skill.path))
  assert.ok(skill.content.length > 1000, '正文过短，可能 frontmatter 解析吞掉了内容')
})

test('parseFrontmatter 处理引号与缺字段', async () => {
  const mod = await importLib()
  const withQuotes = '---\nname: "x"\ndescription: \'y\'\n---\nbody\n'
  const r1 = mod.parseFrontmatter(withQuotes)
  assert.equal(r1.meta.name, 'x')
  assert.equal(r1.meta.description, 'y')
  assert.equal(r1.body.trim(), 'body')

  const noFm = mod.parseFrontmatter('# no frontmatter\n')
  assert.deepEqual(noFm.meta, {})
  assert.match(noFm.body, /no frontmatter/)
})

test('脚本目录包含核心工具', () => {
  const must = [
    'pdx_parser.py',
    'loc_parser.py',
    'cwt_reader.py',
    'build_reference.py',
    'container_exclusivity.py',
    'eu4_lint.py',
    'errorlog_triage.py',
    'new_mod.py',
    'install_mod.py',
    'backup.py',
    'mod_digest.py',
    '_paths.py',
  ]
  for (const f of must) {
    assert.ok(existsSync(join(ROOT, 'skills', 'eu4-modding', 'scripts', f)), `缺脚本 ${f}`)
  }
})

test('SKILL.md §8 声明 reference/ 本地生成', () => {
  const skill = read('skills/eu4-modding/SKILL.md')
  assert.match(skill, /reference/)
  assert.match(skill, /不含任何游戏素材|不含游戏素材/)
})
