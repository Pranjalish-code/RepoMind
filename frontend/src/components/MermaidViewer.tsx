import { useEffect, useRef, useState } from 'react'
import mermaid from 'mermaid'
import { AlertTriangle } from 'lucide-react'

interface MermaidViewerProps {
  code: string
  className?: string
}

let _mermaidInitialized = false

function ensureMermaidInit() {
  if (!_mermaidInitialized) {
    mermaid.initialize({
      startOnLoad: false,
      theme: 'dark',
      themeVariables: {
        primaryColor: '#6172f3',
        primaryTextColor: '#e2e8f0',
        primaryBorderColor: '#4e50e7',
        lineColor: '#64748b',
        sectionBkgColor: '#1e2535',
        altSectionBkgColor: '#252d3d',
        gridColor: '#2e3850',
        secondaryColor: '#252d3d',
        tertiaryColor: '#2e3850',
        background: '#161b27',
        mainBkg: '#1e2535',
        nodeBorder: '#4e50e7',
        clusterBkg: '#252d3d',
        titleColor: '#e2e8f0',
        edgeLabelBackground: '#1e2535',
        fontFamily: 'Inter, system-ui, sans-serif',
      },
      flowchart: {
        curve: 'cardinal',
        htmlLabels: true,
      },
    })
    _mermaidInitialized = true
  }
}

export default function MermaidViewer({ code, className = '' }: MermaidViewerProps) {
  const containerRef = useRef<HTMLDivElement>(null)
  const [error, setError] = useState<string | null>(null)
  const [rendered, setRendered] = useState(false)

  useEffect(() => {
    if (!code || !containerRef.current) return

    ensureMermaidInit()
    setError(null)
    setRendered(false)

    const id = `mermaid-${Date.now()}-${Math.random().toString(36).slice(2)}`

    ;(async () => {
      try {
        const { svg } = await mermaid.render(id, code)
        if (containerRef.current) {
          containerRef.current.innerHTML = svg
          // Make SVG responsive
          const svgEl = containerRef.current.querySelector('svg')
          if (svgEl) {
            svgEl.style.maxWidth = '100%'
            svgEl.style.height = 'auto'
          }
          setRendered(true)
        }
      } catch (err) {
        setError((err as Error).message || 'Failed to render diagram')
      }
    })()
  }, [code])

  return (
    <div className={`relative ${className}`}>
      {error && (
        <div className="flex items-start gap-2 p-3 rounded-lg bg-red-900/20 border border-red-700/40 text-sm text-red-300">
          <AlertTriangle className="w-4 h-4 shrink-0 mt-0.5" />
          <div>
            <p className="font-medium">Diagram render error</p>
            <p className="text-red-400 text-xs mt-1 font-mono">{error}</p>
          </div>
        </div>
      )}
      {!error && !rendered && code && (
        <div className="flex items-center justify-center h-32">
          <div className="w-5 h-5 rounded-full border-2 border-brand-500 border-t-transparent animate-spin" />
        </div>
      )}
      <div
        ref={containerRef}
        className={`mermaid-container transition-opacity duration-300 ${rendered ? 'opacity-100' : 'opacity-0'}`}
      />
    </div>
  )
}
