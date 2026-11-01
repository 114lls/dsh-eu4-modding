/**
 * 分批发推送 —— 绕开单次 tree 的 404（条目数/体积阈值）。
 *
 * 背景：80 个 blob 都建成了，但一次性建 80 条的 tree 返回 404，
 * 而 1 条的 tree 返回 201。说明 GitHub 对单个 tree 请求有隐性阈值
 * （文件数或 payload 体积）。所以拆成多批，每批一个小 tree + 一个 commit，
 * 最后所有内容都在 main 上。
 *
 * 用法： node tools/push_github.mjs
 * 环境： GITHUB_TOKEN
 */
import { readFileSync, readdirSync, statSync } from 'node:fs'
import { extname, join, relative } from 'node:path'

const API = 'https://api.github.com'
const TOKEN = process.env.GITHUB_TOKEN
const OWNER = '114lls'
const REPO = 'dsh-eu4-modding'
const BRANCH = 'main'
const BATCH = 12            // 每批文件数（实测 1 条 OK，80 条 404，取保守值）

const EXCLUDE_DIRS = new Set([
  'reference', '__pycache__', 'node_modules', '_eu4_mod_backups',
  '.git', 'dist', 'coverage', '.pytest_cache', '.vscode', '.idea',
])
const BINARY = new Set(['.png', '.jpg', '.jpeg', '.gif', '.ico', '.webp', '.tgz', '.zip', '.dds', '.tga'])

if (!TOKEN) { console.error('★ 需要 GITHUB_TOKEN'); process.exit(2) }

const H = {
  Authorization: `Bearer ${TOKEN}`,
  Accept: 'application/vnd.github+json',
  'X-GitHub-Api-Version': '2022-11-28',
  'Content-Type': 'application/json',
  'User-Agent': 'dsh-eu4-modding-pusher',
}

async function api(path, opts = {}) {
  const res = await fetch(API + path, { ...opts, headers: H })
  const txt = await res.text()
  if (!res.ok) {
    const e = new Error(`${res.status} ${path}\n${txt.slice(0, 300)}`)
    e.status = res.status
    throw e
  }
  return txt ? JSON.parse(txt) : null
}

function collect(root) {
  const out = []
  const walk = (dir) => {
    for (const name of readdirSync(dir)) {
      const full = join(dir, name)
      const st = statSync(full)
      if (st.isDirectory()) {
        if (EXCLUDE_DIRS.has(name)) continue
        walk(full)
      } else {
        const rel = relative(root, full).split('\\').join('/')
        if (/\.(pyc|pyo)$/.test(name)) continue
        if (/^tmp_/.test(name)) continue
        if (/\.log$/.test(name) && !/fixtures/.test(rel)) continue
        out.push({ rel, full })
      }
    }
  }
  walk(root)
  return out.sort((a, b) => a.rel.localeCompare(b.rel))
}

async function headSha() {
  const ref = await api(`/repos/${OWNER}/${REPO}/git/ref/heads/${BRANCH}`)
  return ref.object.sha
}

async function treeOf(sha) {
  const c = await api(`/repos/${OWNER}/${REPO}/git/commits/${sha}`)
  return c.tree.sha
}

async function main() {
  const files = collect(process.cwd())
  console.log(`待上传 ${files.length} 个文件，每批 ${BATCH} 个`)

  let sha = await headSha()
  let tree = await treeOf(sha)
  console.log(`起点 ${sha.slice(0, 7)}`)

  let batchNo = 0
  for (let i = 0; i < files.length; i += BATCH) {
    const slice = files.slice(i, i + BATCH)
    batchNo += 1

    const entries = []
    for (const f of slice) {
      const buf = readFileSync(f.full)
      const b64 = BINARY.has(extname(f.rel).toLowerCase()) || buf.includes(0)
      const blob = await api(`/repos/${OWNER}/${REPO}/git/blobs`, {
        method: 'POST',
        body: JSON.stringify({
          content: b64 ? buf.toString('base64') : buf.toString('utf8'),
          encoding: b64 ? 'base64' : 'utf-8',
        }),
      })
      entries.push({ path: f.rel, mode: '100644', type: 'blob', sha: blob.sha })
    }

    const newTree = await api(`/repos/${OWNER}/${REPO}/git/trees`, {
      method: 'POST',
      body: JSON.stringify({ base_tree: tree, tree: entries }),
    })

    const commit = await api(`/repos/${OWNER}/${REPO}/git/commits`, {
      method: 'POST',
      body: JSON.stringify({
        message: `chore: add files ${i + 1}-${i + slice.length} (batch ${batchNo})`,
        tree: newTree.sha,
        parents: [sha],
      }),
    })

    await api(`/repos/${OWNER}/${REPO}/git/refs/heads/${BRANCH}`, {
      method: 'PATCH',
      body: JSON.stringify({ sha: commit.sha, force: false }),
    })

    sha = commit.sha
    tree = newTree.sha
    console.log(`  批次 ${batchNo}: ${slice.length} 个文件 → ${commit.sha.slice(0, 7)}`)
  }

  const lastCommit = await api(`/repos/${OWNER}/${REPO}/git/commits/${sha}`)
  console.log(`\n✓ 完成。HEAD = ${sha}`)
  console.log(`  提交件数: ${lastCommit.parents.length > 0 ? '有父提交' : '根提交'}`)
  console.log(`  https://github.com/${OWNER}/${REPO}`)

  // 校验：读回 main 的 tree 递归，数文件
  const full = await api(`/repos/${OWNER}/${REPO}/git/trees/${tree}?recursive=1`)
  const blobs = (full.tree || []).filter((n) => n.type === 'blob')
  console.log(`  远程实际文件数: ${blobs.length}`)
  const missing = files.filter((f) => !blobs.some((b) => b.path === f.rel))
  if (missing.length) {
    console.log(`  ★ 缺失 ${missing.length} 个:`)
    missing.slice(0, 5).forEach((m) => console.log(`     ${m.rel}`))
  } else {
    console.log('  ✓ 全部文件已在远程')
  }
}

main().catch((e) => {
  console.error('★ 失败:', e.message)
  process.exit(1)
})
