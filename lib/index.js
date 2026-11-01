/**
 * dsh-eu4-modding — 把 EU4 模组开发技能装进 DeepSeek Harness，
 * 并把自己的 agent preset 注册进「模式下拉框」。
 *
 * 两条独立的注册路：
 *   1. 技能 → ctx.skills.register()，让模型能用 `skill` 工具加载 eu4-modding
 *   2. agent preset → ctx.agentPresets.register()，让模式下拉框出现「EU4 mod mode」
 *
 * ★ 第 2 条为什么必须由插件做（实测结论）：
 *   `dsh-purge/lib/redteam/index.js:496-499` 的注释原文写着
 *   「Web 0.1.7+ 模式下拉只认 agent-preset registry.register()，
 *     扫磁盘 .agent-presets 不会出现在下拉里」。
 *   我先按「目录扫描」猜过一次，重启后下拉框里没有它 —— 推断被证伪。
 *   所以 preset 必须由插件启动时主动声明。
 *
 * 注意：`reference/`（白名单 JSON，约 2 MB）**不进包**。
 * 它是 vanilla 用法统计，属于游戏内容衍生数据，由用户在本地
 * 跑 build_reference.py 现生。详见 SKILL.md §8。
 */
import { existsSync, readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

import { registerPreset } from './preset.js'

const HERE = dirname(fileURLToPath(import.meta.url))
const SKILL_DIR = join(HERE, '..', 'skills', 'eu4-modding')
const SKILL_FILE = join(SKILL_DIR, 'SKILL.md')

// preset 源文件随包放在 lib/../presets/eu4-modding/
const PRESET_DIR = join(HERE, '..', 'presets', 'eu4-modding')

const FALLBACK = {
  name: 'eu4-modding',
  description:
    'Europa Universalis IV (EU4) 1.37.5.0 modding: create/edit/validate/install/debug mods (events, decisions, missions, ideas, government reforms, map, modifiers, on_actions, localisation) with static checking and error.log triage.',
  whenToUse:
    'Use when the user asks to create, edit, fix, validate, install, publish, or debug an EU4 mod, or asks about EU4 scripting.',
}

export const name = 'eu4-modding-skill'
export const inject = ['skills']

/** 解析 SKILL.md 顶部的小段 YAML frontmatter（name/description/whenToUse）。 */
export function parseFrontmatter(raw) {
  const match = /^---\r?\n([\s\S]*?)\r?\n---\r?\n?/.exec(raw)
  if (!match) return { meta: {}, body: raw }
  const meta = {}
  for (const line of match[1].split(/\r?\n/)) {
    const idx = line.indexOf(':')
    if (idx <= 0) continue
    const key = line.slice(0, idx).trim()
    let value = line.slice(idx + 1).trim()
    if (
      (value.startsWith('"') && value.endsWith('"')) ||
      (value.startsWith("'") && value.endsWith("'"))
    ) {
      value = value.slice(1, -1)
    }
    if (key) meta[key] = value
  }
  return { meta, body: raw.slice(match[0].length) }
}

/** 读打包内的技能并返回给 ctx.skills.register() 的注册对象。 */
export function loadSkill() {
  let raw
  try {
    raw = readFileSync(SKILL_FILE, 'utf8')
  } catch (error) {
    const reason = error instanceof Error ? error.message : String(error)
    throw new Error(`[dsh-eu4-modding] cannot read bundled skill at ${SKILL_FILE}: ${reason}`)
  }
  const { meta, body } = parseFrontmatter(raw)
  const description = meta.description || FALLBACK.description
  const whenToUse = meta.whenToUse || FALLBACK.whenToUse
  return {
    name: meta.name || FALLBACK.name,
    description,
    whenToUse,
    content: body.trimStart(),
    source: 'bundled',
    provider: 'eu4-modding',
    path: SKILL_FILE,
    resourceBase: { kind: 'directory', path: SKILL_DIR },
  }
}

export function apply(ctx) {
  const register = (target) => {
    if (typeof target?.skills?.register !== 'function') {
      target?.logger?.warn?.('[dsh-eu4-modding] skill registry is unavailable; skill not registered')
      return
    }
    target.skills.register(loadSkill())
  }

  if (ctx?.skills?.register) {
    register(ctx)
  } else if (typeof ctx?.inject === 'function') {
    ctx.inject(['skills'], (injected) => register(injected))
  } else {
    register(ctx)
  }

  // ---- agent preset：让「模式下拉框」出现 EU4 mod mode ----
  // 失败不阻塞技能注册（两者独立：技能挂了预设仍可用，反之亦然）。
  try {
    if (!existsSync(join(PRESET_DIR, 'agent.cordis.yml'))) {
      ctx?.logger?.warn?.(`[dsh-eu4-modding] preset 源缺失，跳过注册：${PRESET_DIR}`)
    } else {
      const disposePreset = registerPreset(ctx, PRESET_DIR, ctx?.logger)
      // ctx.effect 在时由它管生命周期；否则注册一次即可（保持卸载路径可用）
      if (typeof ctx?.effect !== 'function') {
        ctx?.logger?.info?.('[dsh-eu4-modding] ctx.effect 不可用，preset 注册不参与生命周期')
      }
      void disposePreset
    }
  } catch (error) {
    ctx?.logger?.warn?.(`[dsh-eu4-modding] preset 注册异常：${error?.message || String(error)}`)
  }
}

export default { name, inject, apply }
