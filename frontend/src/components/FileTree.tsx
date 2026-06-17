import { ChevronRight, ChevronDown, FileCode2, FileText, FileJson, Folder } from 'lucide-react'
import { useState } from 'react'

interface TreeNode {
  name: string
  path: string
  type: 'file' | 'dir'
  children?: TreeNode[]
  language?: string
}

interface FileTreeProps {
  files: { file_path: string; language: string }[]
  onFileSelect?: (path: string) => void
  selectedFile?: string
}

function buildTree(files: { file_path: string; language: string }[]): TreeNode[] {
  const root: TreeNode[] = []

  for (const f of files) {
    const parts = f.file_path.replace(/\\/g, '/').split('/')
    let currentLevel = root

    parts.forEach((part, i) => {
      const isFile = i === parts.length - 1
      const key = parts.slice(0, i + 1).join('/')

      let existingNode = currentLevel.find(node => node.name === part)

      if (!existingNode) {
        existingNode = {
          name: part,
          path: key,
          type: isFile ? 'file' : 'dir',
          language: isFile ? f.language : undefined,
          children: isFile ? undefined : [],
        }
        currentLevel.push(existingNode)
      }

      if (!isFile) {
        currentLevel = existingNode.children!
      }
    })
  }

  const sortNodes = (nodes: TreeNode[]) => {
    nodes.sort((a, b) => {
      if (a.type !== b.type) return a.type === 'dir' ? -1 : 1
      return a.name.localeCompare(b.name)
    })
    nodes.forEach(node => {
      if (node.children) sortNodes(node.children)
    })
  }
  
  sortNodes(root)
  return root
}

function FileIcon({ language }: { language?: string }) {
  if (!language) return <FileText className="w-4 h-4 text-slate-500" />
  const lang = language.toLowerCase()
  if (['python', 'javascript', 'typescript', 'rust', 'go', 'java', 'cpp', 'c', 'tsx', 'jsx'].includes(lang)) {
    return <FileCode2 className="w-4 h-4 text-brand-400" />
  }
  if (lang === 'json') return <FileJson className="w-4 h-4 text-amber-400" />
  return <FileText className="w-4 h-4 text-slate-400" />
}

function TreeNodeItem({
  node,
  depth,
  onFileSelect,
  selectedFile,
}: {
  node: TreeNode
  depth: number
  onFileSelect?: (path: string) => void
  selectedFile?: string
}) {
  const [open, setOpen] = useState(depth < 2)

  if (node.type === 'dir') {
    return (
      <div className="flex flex-col">
        <button
          onClick={() => setOpen(o => !o)}
          style={{ paddingLeft: `${depth * 14 + 10}px` }}
          className="w-full flex items-center gap-1.5 py-1.5 text-left
                     text-slate-300 hover:text-white hover:bg-surface-800 rounded-md text-[13px] transition-colors"
        >
          {open
            ? <ChevronDown className="w-3.5 h-3.5 shrink-0 text-slate-400" />
            : <ChevronRight className="w-3.5 h-3.5 shrink-0 text-slate-400" />
          }
          <Folder className="w-4 h-4 text-brand-500/80" />
          <span className="font-medium truncate tracking-wide">{node.name}</span>
        </button>
        {open && node.children && (
          <div className="relative">
            {/* Indent Guide */}
            <div 
              className="absolute left-0 top-0 bottom-0 border-l border-surface-700/50" 
              style={{ left: `${depth * 14 + 16}px` }}
            />
            {node.children.map(child => (
              <TreeNodeItem
                key={child.path}
                node={child}
                depth={depth + 1}
                onFileSelect={onFileSelect}
                selectedFile={selectedFile}
              />
            ))}
          </div>
        )}
      </div>
    )
  }

  const isSelected = selectedFile === node.path

  return (
    <button
      onClick={() => onFileSelect?.(node.path)}
      style={{ paddingLeft: `${depth * 14 + 28}px` }}
      className={`w-full flex items-center gap-2 py-1.5 text-left rounded-md text-[13px] transition-all duration-200 truncate group
        ${isSelected
          ? 'text-brand-300 bg-brand-500/10 font-medium'
          : 'text-slate-400 hover:text-slate-200 hover:bg-surface-800'
        }`}
    >
      <div className={`transition-transform duration-200 ${isSelected ? 'scale-110' : 'group-hover:scale-110'}`}>
        <FileIcon language={node.language} />
      </div>
      <span className="truncate tracking-wide">{node.name}</span>
    </button>
  )
}

export default function FileTree({ files, onFileSelect, selectedFile }: FileTreeProps) {
  const [search, setSearch] = useState('')

  const filtered = search
    ? files.filter(f => f.file_path.toLowerCase().includes(search.toLowerCase()))
    : files

  const tree = buildTree(filtered)

  return (
    <div className="flex flex-col h-full bg-transparent">
      <div className="px-3 py-3 border-b border-surface-800 bg-surface-900/50">
        <div className="relative">
          <input
            type="text"
            placeholder="Search files..."
            value={search}
            onChange={e => setSearch(e.target.value)}
            className="input-field text-xs py-1.5 px-3 bg-surface-950/50"
          />
        </div>
      </div>
      <div className="flex-1 overflow-y-auto px-2 py-3 custom-scrollbar">
        {tree.length === 0 && (
          <div className="flex flex-col items-center justify-center h-32 opacity-50">
            <FileText className="w-8 h-8 mb-2" />
            <p className="text-sm font-medium">No files found</p>
          </div>
        )}
        {tree.map(node => (
          <TreeNodeItem
            key={node.path}
            node={node}
            depth={0}
            onFileSelect={onFileSelect}
            selectedFile={selectedFile}
          />
        ))}
      </div>
      <div className="px-4 py-2.5 border-t border-surface-800 bg-surface-900/50">
        <p className="text-[11px] font-medium text-slate-500 tracking-wider uppercase">
          {files.length} {files.length === 1 ? 'file' : 'files'} indexed
        </p>
      </div>
    </div>
  )
}
