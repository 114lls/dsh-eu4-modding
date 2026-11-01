/** preset 必填项校验 —— 防止「注册成功但 broken」。
 *
 * 为什么需要它
 * -----------
 * 实测（2026-11-01）：preset 注册进了下拉框，但卡片上带红标「加载失败」。
 * 日志里的真实原因是我漏了三个**必填** config 字段：
 *
 *   tool-fs-search : $.sampleOverCapGlobResults missing required value
 *   planning       : PlanModeConfig needs a non-empty `section`
 *   tool-todo      : $.allowParallelInProgress missing required value
 *
 * 这类失败很隐蔽：
 *   · 注册调用成功（register 返回了 dispose 函数）
 *   · 下拉框里出现了卡片
 *   · 只有点进去才发现是 broken
 *
 * 所以「注册成功」和「能用」是两件事。这个测试把「能用」的判据固化下来：
 * 逐条比对 redteam（已知可用）里每个插件行的 config 必填字段。
 *
 * 跑法：node --test tests/preset-required.test.mjs
 */
import assert from 'node:assert/strict'
import { existsSync, readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { test } from 'node:test'
import { fileURLToPath, pathToFileURL } from 'node:url'

const HERE = dirname(fileURLToPath(import.meta.url))
const ROOT = join(HERE, '..')
const MINE = join(ROOT, 'presets', 'eu4-modding', 'agent.cordis.yml')
const REDTEAM = join(
  process.env.USERPROFILE || process.env.HOME || '',
  '.dsh', '.agent-presets', 'redteam', 'agent.cordis.yml',
)

const importPreset = async () =>
  import(pathToFileURL(join(ROOT, 'lib', 'preset.js')).href)

/** 从宿主报错里提取到的「必填字段」契约。新增 preset 行时按需补充。 */
const REQUIRED = {
  'tool-fs-search': ['sampleOverCapGlobResults'],
  'tool-todo': ['allowParallelInProgress'],
}

/** 这些插件需要 config.section 非空（宿主会校验）。 */
const NEEDS_SECTION = ['@deepseek-ai/dsh-plan-mode']

/**
 * ★ 会发布「进程级服务」的插件必须包在 isolate realm 里。
 *
 * 宿主校验原文（实测两次报错）：
 *   「Preset services require isolate realms: compaction, toolResultPruner」
 *   「Preset services require isolate realms: ...」
 *
 * 不包 isolate → 服务落到 root realm → 与别的 preset 撞名 → 整份 preset 被标 broken。
 * 这个错误只在注册后才暴露，卡片上表现为红字「加载失败」。
 *
 * 格式：{ 插件 name: [需要 isolate 的服务名...] }
 */
const NEEDS_ISOLATE = {
  '@deepseek-ai/dsh-compaction-basic': ['compaction'],
  '@deepseek-ai/dsh-compaction-tool-result-pruner': ['toolResultPruner'],
  '@deepseek-ai/dsh-plan-mode': ['planMode'],
}

/** 摊平后的每行，记录它所在环境（含祖先组）声明的 isolate 服务。 */
function collectIsolateServices(rows, inherited = {}) {
  const out = []
  for (const r of rows ?? []) {
    if (!r || typeof r !== 'object') continue
    const iso = r.isolate && typeof r.isolate === 'object' && !Array.isArray(r.isolate)
      ? { ...inherited, ...r.isolate }
      : inherited
    out.push({ row: r, iso })
    if (Array.isArray(r.config)) out.push(...collectIsolateServices(r.config, iso))
  }
  return out
}

/**
 * ★ 明确**不该**从 redteam 抄的 config 字段，逐条给理由。
 *
 * 对照测试的意义是「找出我漏掉的必填项」，不能退化成「redteam 有什么我就要有什么」
 * —— 有些字段是红队语义专用的，照抄会带来错误行为。
 */
const INTENTIONALLY_OMITTED = {
  'skill-filesystem': {
    customSkillDirs:
      'redteam 用它把技能根收窄到 $DSH_HOME/skills + $DSH_HOME/redteam/skills，'
      + '并用 !!js dshHomePath(...) 计算。eu4 模式相反：要保持 includeDefaultRoots=true，'
      + '让模组作者放在 .agents/skills 的技能也能出现。照抄会把用户技能挡在外面。',
    includeDefaultRoots:
      'redteam 设为 false（怕几百个技能污染上下文）；eu4 设为 true（怕漏掉用户的）。'
      + '这是刻意的相反取舍。',
  },
}

async function loadMine() {
  const { readPresetDefinition } = await importPreset()
  return readPresetDefinition('eu4-modding', join(ROOT, 'presets', 'eu4-modding'))
}

function flatten(rows) {
  /** 把 cordis:group 的子行也摊平，便于按 id 查找。 */
  const out = []
  for (const r of rows ?? []) {
    if (!r || typeof r !== 'object') continue
    out.push(r)
    if (Array.isArray(r.config)) out.push(...flatten(r.config))
  }
  return out
}

test('所有已知必填字段都填了', async () => {
  const def = await loadMine()
  const rows = flatten(def.plugins)
  const missing = []
  for (const r of rows) {
    const need = REQUIRED[r.id]
    if (!need) continue
    for (const field of need) {
      if (r.config?.[field] === undefined) {
        missing.push(`${r.id} 缺 config.${field}`)
      }
    }
  }
  assert.deepEqual(missing, [],
    '这些必填 config 字段缺失，会导致 preset broken（注册成功但加载失败）：\n  '
    + missing.join('\n  '))
})

test('plan-mode 有非空 section', async () => {
  const def = await loadMine()
  const rows = flatten(def.plugins)
  const planRows = rows.filter((r) => NEEDS_SECTION.includes(r.name))
  assert.ok(planRows.length > 0, '没有 dsh-plan-mode 行')
  for (const r of planRows) {
    const section = r.config?.section
    assert.equal(typeof section, 'string',
      `${r.name} 的 config.section 必须是字符串（宿主：PlanModeConfig needs a non-empty section）`)
    assert.ok(section.length > 200, `${r.name} 的 section 太短（${section.length}）`)
  }
})

test('plan-mode 必须包在 isolate 组里', async () => {
  const def = await loadMine()
  const group = def.plugins.find((r) => r.id === 'planning')
  assert.ok(group, '缺 planning 组')
  assert.equal(group.name, 'cordis:group', 'planning 必须是 cordis:group（不能是扁平行）')
  assert.equal(group.isolate?.planMode, true, '缺 isolate.planMode: true')
  const inner = group.config?.find?.((r) => r.id === 'plan-mode')
  assert.ok(inner, '组里没有 plan-mode 子行')
})

test('★ 需要 isolate 的插件，服务名都已声明（防 compaction broken）', async () => {
  const def = await loadMine()
  const flat = collectIsolateServices(def.plugins)
  const problems = []
  for (const { row, iso } of flat) {
    const need = NEEDS_ISOLATE[row.name]
    if (!need) continue
    for (const svc of need) {
      if (!iso[svc]) {
        problems.push(`${row.name} 需要 isolate.${svc}，但它所在环境没声明`)
      }
    }
  }
  assert.deepEqual(problems, [],
    '这些插件会发布进程级服务但没在 isolate realm 里声明，会导致 preset broken：\n  '
    + problems.join('\n  '))
})

test('compaction 组结构正确', async () => {
  const def = await loadMine()
  const g = def.plugins.find((r) => r.id === 'compaction')
  assert.ok(g, '缺 compaction 组')
  assert.equal(g.name, 'cordis:group')
  assert.equal(g.isolate?.compaction, true)
  assert.equal(g.isolate?.toolResultPruner, true)
  const ids = (g.config ?? []).map((r) => r.id)
  for (const need of ['compaction-basic', 'command-compact', 'tool-result-pruner']) {
    assert.ok(ids.includes(need), `compaction 组里缺 ${need}，实际 ${ids.join(',')}`)
  }
})

test('布尔字段类型正确（sampleOverCapGlobResults 必须是布尔）', async () => {
  const def = await loadMine()
  const row = flatten(def.plugins).find((r) => r.id === 'tool-fs-search')
  assert.ok(row, '缺 tool-fs-search')
  assert.equal(typeof row.config.sampleOverCapGlobResults, 'boolean',
    '宿主 schema 要布尔，写成字符串会 broken')
})

test('与 redteam 对照：每个插件行的 config 键集合不应少于它', async () => {
  if (!existsSync(REDTEAM)) {
    console.log('  [skip] 找不到 redteam preset，跳过对照')
    return
  }
  const { readPresetDefinition } = await importPreset()
  // 用宽松模式读 redteam：它含 !!js 标签，我们的解析器严格模式会抛错。
  // 这里只是结构对照，不做真实挂载，所以宽松是安全的。
  const ref = readPresetDefinition('redteam', dirname(REDTEAM), { lenientTags: true })
  const refRows = flatten(ref.plugins)
  const myRows = flatten((await loadMine()).plugins)

  const refById = new Map(refRows.map((r) => [r.id, r]))
  const problems = []
  const excused = []
  for (const r of myRows) {
    const other = refById.get(r.id)
    if (!other || !other.config || typeof other.config !== 'object') continue
    if (Array.isArray(other.config)) continue
    const omit = INTENTIONALLY_OMITTED[r.id] ?? {}
    for (const k of Object.keys(other.config)) {
      if (r.config?.[k] !== undefined) continue
      if (omit[k]) {
        excused.push(`${r.id}.${k}`)
        continue
      }
      problems.push(`${r.id} 缺 ${k}（redteam 有）`)
    }
  }
  if (excused.length) {
    console.log(`  [刻意不抄] ${excused.join(', ')}（理由见 INTENTIONALLY_OMITTED）`)
  }
  assert.deepEqual(problems, [], '以下 config 字段 redteam 有而我没有：\n  ' + problems.join('\n  '))
})

test('preset 能被解析且 id/name/order 正确', async () => {
  const def = await loadMine()
  assert.equal(def.id, 'eu4-modding')
  assert.equal(def.name, 'EU4 mod mode')
  assert.equal(def.order, 11)
})

test('agent.cordis.yml 里没有 !!js 标签（本解析器不支持）', () => {
  const text = readFileSync(MINE, 'utf8')
  assert.ok(!text.includes('!!js'),
    '用了 !!js 会让我的 YAML 子集解析器抛错；需要时先扩展解析器')
})
