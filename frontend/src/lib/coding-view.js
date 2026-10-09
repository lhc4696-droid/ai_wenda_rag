function compareNodes(left, right) {
  if (left.kind !== right.kind) return left.kind === 'directory' ? -1 : 1
  const leftName = left.name.toLowerCase()
  const rightName = right.name.toLowerCase()
  if (leftName !== rightName) return leftName < rightName ? -1 : 1
  return left.name === right.name ? 0 : left.name < right.name ? -1 : 1
}

// Presentation only: paths have already passed the local file access validation.
// This tree never reads files or treats recovery records as the live directory.
export function buildCodingFileTree(files, changes, filter = '', collapsedFolders = new Set()) {
  const query = filter.trim().toLowerCase()
  const changedPaths = new Set(changes.map((change) => change.path))
  const root = new Map()

  for (const file of files) {
    if (query && !file.path.toLowerCase().includes(query)) continue
    const parts = file.path.split('/')
    let children = root
    for (let index = 0; index < parts.length; index += 1) {
      const name = parts[index]
      const path = parts.slice(0, index + 1).join('/')
      const kind = index === parts.length - 1 ? 'file' : 'directory'
      if (!children.has(name)) {
        children.set(name, { path, name, kind, children: new Map() })
      }
      children = children.get(name).children
    }
  }

  const rows = []
  function visit(children, depth) {
    for (const node of [...children.values()].sort(compareNodes)) {
      const row = {
        path: node.path,
        name: node.name,
        depth,
        kind: node.kind,
        changed: node.kind === 'file' && changedPaths.has(node.path),
      }
      if (node.kind === 'directory') {
        row.expanded = Boolean(query) || !collapsedFolders.has(node.path)
      }
      rows.push(row)
      if (row.expanded) visit(node.children, depth + 1)
    }
  }
  visit(root, 0)
  return rows
}
