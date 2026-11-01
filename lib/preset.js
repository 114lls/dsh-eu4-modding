/**
 * 读取并注册 eu4-modding agent preset。
 *
 * 为什么需要这个文件
 * ----------------
 * 实测结论（`dsh-purge/lib/redteam/index.js:496-499` 的注释原文）：
 *   「Web 0.1.7+ 模式下拉只认 agent-preset registry.register()，
 *     扫磁盘 .agent-presets 不会出现在下拉里」
 *
 * 所以光把目录放进 `~/.dsh/.agent-presets/eu4-modding/` **不会出现在下拉框**
 * —— 必须由插件在启动时调用 `ctx.agentPresets.register(definition)`。
 * 这条我先按「目录扫描」猜过一次，实测被证伪（重启后下拉里没有它）。
 *
 * definition 的形状（与宿主 readPresetDefinition 一致）：
 *   {
 *     id: 'eu4-modding',
 *     name: '...',            // 来自 preset.yml 的单行字符串
 *     description: '...',     // 同上
 *     order: 11,              // 数字
 *     plugins: [ {...row}, ... ]   // agent.cordis.yml 的顶层列表
 *   }
 *
 * ⚠ 我不复刻宿主的完整 YAML 解析（它支持 `!!js` 标签等扩展）。
 *   这里只解析本 preset 实际用到的最小子集：块序列 + 标量 + 嵌套映射 + 块文本 `|`。
 *   遇到不支持的结构**直接抛错**，不静默降级 —— 挂载失败比静默错配好排查。
 */
import { existsSync, readFileSync } from 'node:fs'
import { join } from 'node:path'

export const PRESET_ID = 'eu4-modding'

/** 极简 YAML 子集解析：够读 preset.yml（映射）与 agent.cordis.yml（顶层序列）。
 *
 * @param text              YAML 文本
 * @param opts.lenientTags  为 true 时，`!!js` 这类标签**保留原文字符串**而不抛错。
 *   用途：只做结构对照（例如拿本解析器去读 redteam 的 preset 比对 config 键集合）。
 *   本插件自己注册 preset 时一律用严格模式 —— 宁可挂载失败，也不要把一个
 *   `!!js` 函数表达式当成字符串静默塞进 config。
 */
export function readYamlSubset(text, opts = {}) {
  const lenientTags = opts.lenientTags === true
  const raw = String(text ?? '').replace(/\r\n/g, '\n').replace(/\r/g, '\n')
  const lines = raw.split('\n')

  // 去掉纯注释与空行会影响缩进基准，但块文本（|）里不能去，所以按需处理。
  let i = 0

  const indentOf = (s) => s.length - s.trimStart().length
  const isBlank = (s) => s.trim() === '' || s.trimStart().startsWith('#')

  function skipBlank() {
    while (i < lines.length && isBlank(lines[i])) i += 1
  }

  function parseBlockScalar(parentIndent) {
    // 调用时 i 指向 `|` 所在行的下一行；收集所有缩进 > parentIndent 的行
    const buf = []
    let baseIndent = null
    while (i < lines.length) {
      const ln = lines[i]
      if (ln.trim() === '') {
        buf.push('')
        i += 1
        continue
      }
      const ind = indentOf(ln)
      if (ind <= parentIndent) break
      if (baseIndent === null) baseIndent = ind
      buf.push(ln.slice(Math.min(baseIndent, ind)))
      i += 1
    }
    // 去掉尾部空行
    while (buf.length && buf[buf.length - 1] === '') buf.pop()
    return buf.join('\n')
  }

  function parseScalar(s) {
    const t = s.trim()
    if (t === '') return null
    if (t === 'true') return true
    if (t === 'false') return false
    if (t === 'null' || t === '~') return null
    if (/^-?\d+$/.test(t)) return Number.parseInt(t, 10)
    if (/^-?\d+\.\d+$/.test(t)) return Number.parseFloat(t)
    if ((t.startsWith("'") && t.endsWith("'")) || (t.startsWith('"') && t.endsWith('"'))) {
      return t.slice(1, -1)
    }
    if (t.startsWith('[') && t.endsWith(']')) {
      const inner = t.slice(1, -1).trim()
      if (!inner) return []
      return inner.split(',').map((x) => parseScalar(x))
    }
    if (t.startsWith('!!')) {
      if (lenientTags) {
        // 宽松模式：把标签连同表达式原样保留为字符串（只用于结构对照，不会真的喂给宿主）
        return t
      }
      throw new Error(
        `不支持 YAML 标签：${t}（本 preset 不应使用 !!js；` +
        `若只是做结构对照，用 readYamlSubset(text, { lenientTags: true })）`,
      )
    }
    return t
  }

  /** 解析缩进为 ind 的映射/序列，直到缩进变浅。 */
  function parseNode(ind) {
    skipBlank()
    if (i >= lines.length) return null
    const first = lines[i]
    const firstInd = indentOf(first)
    if (firstInd < ind) return null
    const trimmed = first.trimStart()
    if (trimmed.startsWith('- ')) return parseSeq(firstInd)
    return parseMap(firstInd)
  }

  function parseSeq(ind) {
    const arr = []
    while (i < lines.length) {
      skipBlank()
      if (i >= lines.length) break
      const ln = lines[i]
      const curInd = indentOf(ln)
      if (curInd !== ind) break
      const t = ln.trimStart()
      if (!t.startsWith('- ')) break
      const rest = t.slice(2)
      if (rest.trim() === '') {
        // 「- 」后面换行，值在下一层
        i += 1
        arr.push(parseNode(ind + 2))
      } else if (rest.includes(':') && !rest.startsWith('"') && !rest.startsWith("'")) {
        // 序列项本身是一个映射首行：- id: xxx
        // 把它当成一个缩进 ind+2 的映射，但首行已在 rest 里
        arr.push(parseMapItemInline(ind, rest))
      } else {
        arr.push(parseScalar(rest))
        i += 1
      }
    }
    return arr
  }

  /** `- key: value` 形式的序列项：先吃掉首行，再继续缩进更深的键。 */
  function parseMapItemInline(seqInd, rest) {
    const map = {}
    const keyInd = seqInd + 2
    // ★ 首行 `- id: xxx`：i 此时指向这一行。readKeyValueInto 内部会 i += 1，
    //   所以这里**不能**先自增 —— 早期版本先 i+=1 再调用，导致紧随其后的
    //  同级键（name: / config:）整行被跳过，表现为「每项都缺 name」。
    readKeyValueInto(map, rest, keyInd)
    // 继续读同一项的后续键（缩进 == keyInd）
    while (i < lines.length) {
      skipBlank()
      if (i >= lines.length) break
      const ln = lines[i]
      const curInd = indentOf(ln)
      if (curInd !== keyInd) break
      const t = ln.trimStart()
      if (t.startsWith('- ')) break
      readKeyValueInto(map, t, keyInd)
    }
    return map
  }

  function readKeyValueInto(obj, lineText, ind) {
    const m = /^([^:#]+):(?:\s+(.*))?$/.exec(lineText.trim())
    if (!m) throw new Error(`无法解析的行：${lineText}`)
    const key = m[1].trim()
    const valuePart = (m[2] ?? '').trim()
    if (valuePart === '' || valuePart === '|' || valuePart === '>') {
      i += 1
      if (valuePart === '|' || valuePart === '>') {
        obj[key] = parseBlockScalar(ind)
      } else {
        const child = parseNode(ind + 2)
        obj[key] = child
      }
    } else {
      obj[key] = parseScalar(valuePart)
      i += 1
    }
  }

  function parseMap(ind) {
    const obj = {}
    while (i < lines.length) {
      skipBlank()
      if (i >= lines.length) break
      const ln = lines[i]
      const curInd = indentOf(ln)
      if (curInd < ind) break
      if (curInd > ind) break
      const t = ln.trimStart()
      if (t.startsWith('- ')) break
      readKeyValueInto(obj, t, ind)
    }
    return obj
  }

  return parseNode(0)
}

/** 读 preset 目录，生成 registry.register() 需要的 definition。 */
export function readPresetDefinition(id, dir, opts = {}) {
  const metaPath = join(dir, 'preset.yml')
  const compPath = join(dir, 'agent.cordis.yml')
  if (!existsSync(metaPath)) throw new Error(`缺 preset.yml：${metaPath}`)
  if (!existsSync(compPath)) throw new Error(`缺 agent.cordis.yml：${compPath}`)

  const meta = readYamlSubset(readFileSync(metaPath, 'utf8'), opts)
  if (!meta || typeof meta !== 'object' || Array.isArray(meta)) {
    throw new Error('preset.yml 必须是映射')
  }
  const plugins = readYamlSubset(readFileSync(compPath, 'utf8'), opts)
  if (!Array.isArray(plugins)) {
    throw new Error('agent.cordis.yml 必须是顶层列表（每项一行插件）')
  }

  const definition = { id, plugins }
  if (typeof meta.name === 'string') definition.name = meta.name
  if (typeof meta.description === 'string') definition.description = meta.description
  if (typeof meta.order === 'number') definition.order = meta.order
  return definition
}

/** 软获取 registry：优先 ctx.agentPresets，其次 ctx.get('agentPresets')。 */
export function softRegistry(ctx) {
  try {
    if (ctx?.agentPresets && typeof ctx.agentPresets.register === 'function') {
      return ctx.agentPresets
    }
  } catch { /* without inject */ }
  try {
    const reg = ctx?.get?.('agentPresets')
    if (reg && typeof reg.register === 'function') return reg
  } catch { /* missing */ }
  return null
}

/**
 * 把 preset 注册进模式下拉。
 *
 * 与 dsh-purge 的做法一致：
 *   · 用 ctx.effect 管理生命周期（卸载时自动 undeclare）
 *   · registry 可能还没就绪 → 每 400ms 重试，最多 ~10s
 *   · 注册后用 list() 查 broken，把结果如实报告（不假装成功）
 *
 * @returns 是否已经尝试注册（不代表成功，成功与否看日志）
 */
export function registerPreset(ctx, presetDir, logger) {
  const log = (m) => logger?.info?.(`[dsh-eu4-modding] ${m}`)
  const warn = (m) => logger?.warn?.(`[dsh-eu4-modding] ${m}`)

  let release
  let closed = false
  const undeclare = async () => {
    const dispose = release
    release = undefined
    if (!dispose) return
    try {
      await dispose()
    } catch (error) {
      warn(`undeclare failed: ${error?.message || String(error)}`)
    }
  }

  const once = (registry) => {
    let definition
    try {
      definition = readPresetDefinition(PRESET_ID, presetDir)
    } catch (error) {
      warn(`读 preset 失败（不会出现在下拉框）：${error?.message || String(error)}`)
      return
    }
    Promise.resolve()
      .then(async () => {
        await undeclare()
        release = await registry.register(definition)
        let broken = null
        try {
          const row = (await registry.list())?.find?.((p) => p.id === PRESET_ID)
          broken = row?.broken || null
        } catch { /* list 不可用时忽略 */ }
        if (broken) warn(`preset 已注册但 broken：${broken}`)
        else log(`preset 已注册到模式下拉：${definition.name || PRESET_ID}（${definition.plugins.length} 个插件行）`)
      })
      .catch((error) => {
        warn(`register 失败：${error?.message || String(error)}`)
      })
  }

  const tick = () => {
    if (closed) return true
    const registry = softRegistry(ctx)
    if (!registry) return false
    once(registry)
    return true
  }

  const dispose = ctx.effect(() => {
    if (tick()) return () => { closed = true }
    let tries = 0
    const timer = setInterval(() => {
      tries += 1
      if (tick() || tries > 25) {
        clearInterval(timer)
        if (tries > 25) warn('等不到 agentPresets registry，preset 未注册')
      }
    }, 400)
    if (typeof timer.unref === 'function') timer.unref()
    return () => {
      closed = true
      clearInterval(timer)
    }
  })

  return () => {
    closed = true
    void undeclare()
    try { dispose?.() } catch { /* ignore */ }
  }
}
