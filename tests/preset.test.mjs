/** preset 解析与注册契约的测试。
 *
 * 为什么需要它
 * -----------
 * 我自己写的 YAML 子集解析器踩过一个真实 bug：
 * `parseMapItemInline` 里先 `i += 1` 再调 `readKeyValueInto`（它内部也会 `i += 1`），
 * 结果紧随 `- id: xxx` 之后**同级键（name: / config:）整行被跳过**。
 * 表现为「19 个插件行全部缺 name」—— preset 会注册失败但不报原因。
 *
 * 这个 bug 不会让程序崩溃，只会让下拉框里没有它。所以必须用测试钉住。
 *
 * 跑法：node --test tests/preset.test.mjs
 */
import assert from 'node:assert/strict'
import { existsSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { test } from 'node:test'
import { fileURLToPath, pathToFileURL } from 'node:url'

const HERE = dirname(fileURLToPath(import.meta.url))
const ROOT = join(HERE, '..')
const PRESET_DIR = join(ROOT, 'presets', 'eu4-modding')

const importPreset = async () =>
  import(pathToFileURL(join(ROOT, 'lib', 'preset.js')).href)

test('preset 源文件存在', () => {
  assert.ok(existsSync(join(PRESET_DIR, 'preset.yml')), '缺 preset.yml')
  assert.ok(existsSync(join(PRESET_DIR, 'agent.cordis.yml')), '缺 agent.cordis.yml')
})

test('preset.yml 可解析且字段类型正确', async () => {
  const { readYamlSubset } = await importPreset()
  const { readFileSync } = await import('node:fs')
  const meta = readYamlSubset(readFileSync(join(PRESET_DIR, 'preset.yml'), 'utf8'))
  assert.equal(typeof meta, 'object')
  assert.equal(meta.name, 'EU4 mod mode')
  assert.equal(typeof meta.order, 'number', 'order 必须是数字（宿主会校验）')
  assert.equal(typeof meta.description, 'string')
  assert.ok(!meta.description.includes('\n'), 'description 必须单行（宿主会校验）')
})

test('agent.cordis.yml 解析出结构正确的插件行', async () => {
  const { readPresetDefinition } = await importPreset()
  const def = readPresetDefinition('eu4-modding', PRESET_DIR)
  assert.equal(def.id, 'eu4-modding')

  // ★ 不断言具体行数：行数随重构而变（19 → 21 只因为 compaction 改成了组），
  //   硬编码会制造「一改结构就红」的假信号 —— 那正是本项目反复强调要避免的。
  //   改断言「结构性质」：必需的组都在，且组真的是组。
  assert.ok(def.plugins.length >= 10, `插件行太少（${def.plugins.length}）`)

  const ids = def.plugins.map((p) => p.id)
  for (const need of ['persona', 'skill-filesystem', 'tool-skill',
    'compaction', 'planning', 'delegation']) {
    assert.ok(ids.includes(need), `缺必需插件组 ${need}（实际有 ${ids.join(',')}）`)
  }

  for (const gid of ['compaction', 'planning', 'delegation']) {
    const g = def.plugins.find((p) => p.id === gid)
    assert.equal(g.name, 'cordis:group', `${gid} 必须是 cordis:group`)
    assert.equal(g.group, true, `${gid} 必须 group: true`)
    assert.ok(Array.isArray(g.config), `${gid}.config 应是子行数组`)
  }
})

test('★ 每个插件行都必须有 name（防 parseMapItemInline 回归）', async () => {
  const { readPresetDefinition } = await importPreset()
  const def = readPresetDefinition('eu4-modding', PRESET_DIR)
  const noName = def.plugins.filter((p) => !p || typeof p.name !== 'string' || !p.name)
  assert.deepEqual(
    noName.map((p) => p?.id ?? p),
    [],
    '这些插件行缺 name —— 检查 parseMapItemInline 是否又跳过了同级键',
  )
})

test('每个插件行必须有 id', async () => {
  const { readPresetDefinition } = await importPreset()
  const def = readPresetDefinition('eu4-modding', PRESET_DIR)
  const noId = def.plugins.filter((p) => !p || typeof p.id !== 'string')
  assert.deepEqual(noId, [], '有插件行缺 id')
})

test('persona 的块文本 prefix 完整保留', async () => {
  const { readPresetDefinition } = await importPreset()
  const def = readPresetDefinition('eu4-modding', PRESET_DIR)
  const persona = def.plugins.find((p) => p.id === 'persona')
  assert.ok(persona, '没有 persona 行')
  const prefix = persona.config?.prefix
  assert.equal(typeof prefix, 'string', 'prefix 必须是字符串')
  assert.ok(prefix.length > 100, `prefix 太短（${prefix.length}），块文本可能被截断`)
  assert.ok(prefix.startsWith('## 你是谁'), `prefix 首行错：${prefix.slice(0, 20)}`)
  // 只检查「首行不是 YAML 块标记」——正文里出现 | 是正常的（表格语法等），不能禁
  assert.notEqual(prefix.trim(), '|', 'prefix 是裸的块标记，说明没解析出内容')
  assert.ok(!prefix.startsWith('|\n'), 'prefix 以块标记开头，说明块文本未被剥离')
})

test('嵌套 config 能正确解析（delegation 组）', async () => {
  const { readPresetDefinition } = await importPreset()
  const def = readPresetDefinition('eu4-modding', PRESET_DIR)
  const delegation = def.plugins.find((p) => p.id === 'delegation')
  assert.ok(delegation, '没有 delegation 行')
  assert.equal(delegation.name, 'cordis:group')
  assert.equal(delegation.group, true, 'group 必须是布尔 true')
  assert.ok(Array.isArray(delegation.config), 'delegation.config 应是数组（子行列表）')
  assert.ok(delegation.config.length >= 2, 'delegation 子行太少')
})

test('不支持的 YAML 标签会明确报错，不静默降级', async () => {
  const { readYamlSubset } = await importPreset()
  assert.throws(
    () => readYamlSubset('a: !!js whatever()'),
    /不支持 YAML 标签/,
    '遇到 !!js 应抛错（本 preset 不应使用它）',
  )
})

test('readPresetDefinition 缺文件时报错而不是返回空', async () => {
  const { readPresetDefinition } = await importPreset()
  assert.throws(
    () => readPresetDefinition('nope', join(ROOT, 'no-such-dir')),
    /缺 preset\.yml/,
  )
})
