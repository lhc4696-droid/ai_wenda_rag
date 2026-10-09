import test from 'node:test'
import assert from 'node:assert/strict'
import { buildCodingFileTree } from '../src/lib/coding-view.js'

const files = [
  { path: 'README.md', size: 32 },
  { path: 'src/main.js', size: 16 },
  { path: 'src/components/Button.vue', size: 24 },
  { path: 'package.json', size: 12 },
  { path: 'src/App.vue', size: 8 },
  { path: 'docs/guide.md', size: 10 },
]

test('directory rows precede files at every depth with stable hierarchical sorting', () => {
  const result = buildCodingFileTree(files, [])
  assert.deepEqual(
    result.map(({ path, depth, kind }) => ({ path, depth, kind })),
    [
      { path: 'docs', depth: 0, kind: 'directory' },
      { path: 'docs/guide.md', depth: 1, kind: 'file' },
      { path: 'src', depth: 0, kind: 'directory' },
      { path: 'src/components', depth: 1, kind: 'directory' },
      { path: 'src/components/Button.vue', depth: 2, kind: 'file' },
      { path: 'src/App.vue', depth: 1, kind: 'file' },
      { path: 'src/main.js', depth: 1, kind: 'file' },
      { path: 'package.json', depth: 0, kind: 'file' },
      { path: 'README.md', depth: 0, kind: 'file' },
    ],
  )
  assert.deepEqual(buildCodingFileTree([...files].reverse(), []), result)
  assert.equal(result.find((row) => row.path === 'src/components').name, 'components')
  assert.equal(result.find((row) => row.path === 'src/components').expanded, true)
  assert.equal('expanded' in result.find((row) => row.path === 'README.md'), false)
})

test('full-path filtering is case insensitive and includes only necessary ancestors', () => {
  assert.deepEqual(
    buildCodingFileTree(files, [], ' COMPONENTS/buTToN ').map((row) => row.path),
    ['src', 'src/components', 'src/components/Button.vue'],
  )
  assert.deepEqual(
    buildCodingFileTree(files, [], '.VUE').map((row) => row.path),
    ['src', 'src/components', 'src/components/Button.vue', 'src/App.vue'],
  )
  assert.deepEqual(buildCodingFileTree(files, [], 'does-not-exist'), [])
})

test('collapsed folders keep their row but hide their descendants', () => {
  const collapsed = new Set(['src/components'])
  const result = buildCodingFileTree(files, [], '', collapsed)
  assert.equal(result.find((row) => row.path === 'src/components').expanded, false)
  assert.equal(
    result.some((row) => row.path === 'src/components/Button.vue'),
    false,
  )
  assert.equal(
    result.some((row) => row.path === 'src/main.js'),
    true,
  )
  assert.deepEqual(
    buildCodingFileTree(files, [], '', new Set(['src'])).map((row) => row.path),
    ['docs', 'docs/guide.md', 'src', 'package.json', 'README.md'],
  )
  assert.deepEqual([...collapsed], ['src/components'])
})

test('search temporarily expands matching paths without changing the collapsed set', () => {
  const collapsed = new Set(['src', 'src/components'])
  const result = buildCodingFileTree(files, [], 'button', collapsed)
  assert.deepEqual(
    result.map((row) => row.path),
    ['src', 'src/components', 'src/components/Button.vue'],
  )
  assert.equal(
    result.filter((row) => row.kind === 'directory').every((row) => row.expanded),
    true,
  )
  assert.deepEqual([...collapsed], ['src', 'src/components'])
})

test('change markers apply only to listed files and do not invent recovery-only paths', () => {
  const changes = [
    { path: 'src/App.vue', phase: 'applied' },
    { path: 'src', phase: 'applied' },
    { path: 'old-project/removed.py', phase: 'applied' },
  ]
  const result = buildCodingFileTree(files, changes)
  assert.deepEqual(
    result.filter((row) => row.changed).map((row) => row.path),
    ['src/App.vue'],
  )
  assert.equal(
    result.some((row) => row.path.startsWith('old-project')),
    false,
  )
  assert.equal(
    result.filter((row) => row.kind === 'directory').every((row) => !row.changed),
    true,
  )
  assert.deepEqual(buildCodingFileTree([], changes), [])
})

test('duplicate file paths produce one row without mutating input records', () => {
  const input = [
    Object.freeze({ path: 'main.js', size: 4 }),
    Object.freeze({ path: 'main.js', size: 4 }),
  ]
  const result = buildCodingFileTree(Object.freeze(input), Object.freeze([]))
  assert.deepEqual(result, [
    { path: 'main.js', name: 'main.js', depth: 0, kind: 'file', changed: false },
  ])
})
