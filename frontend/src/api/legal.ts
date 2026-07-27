import { apiFetch } from './client'

// ---------------------------------------------------------------------------
// Types (mirror openkb.legal.* / openkb.sync.* / openkb.visual.* shapes)
// ---------------------------------------------------------------------------
export interface GraphNode {
  node_id: string
  node_type: string
  label: string
  description?: string | null
  source_page?: string | null
  authority_level?: string | null
  status?: string
  aliases?: string[]
  tags?: string[]
}
export interface GraphEdge {
  edge_id: string
  source_id: string
  target_id: string
  relation_type: string
  weight: number
  confidence: number
}
export interface ImpactNode {
  node: GraphNode
  depth: number
  via: string[]
}
export interface LifecycleEntry {
  page_path: string
  status: string
  confidence: number
  sources_count: number
  decay_rate: string
  superseded_by?: string | null
  last_confirmed?: string | null
}
export interface SyncSourceStat {
  source_id: string
  name: string
  type: string
  enabled: boolean
  file_count: number
  last_sync?: string | null
}
export interface LegalGraphEdge {
  edge_id: string
  source_id: string
  target_id: string
  relation_type: string
  weight: number
  confidence: number
}
export interface LegalGraphData {
  nodes: GraphNode[]
  edges: LegalGraphEdge[]
}
export function getLegalGraph(kb: string): Promise<LegalGraphData> {
  return apiFetch(`/api/v1/legal/graph?kb=${encodeURIComponent(kb)}`)
}

// DocIR recursive node (mirrors openkb.docir.DocIRNode.to_dict()).
export interface DocirNode {
  id: string
  kind: string
  title?: string | null
  text?: string
  children?: DocirNode[]
  loc?: { page?: number | null } | null
  vision?: { type?: string; text_anchor?: string | null; render_ref?: string | null } | null
}
export interface DocirData {
  docir_version?: string
  doc_id?: string
  doc_name?: string
  root?: DocirNode | null
  vision_nodes?: string[]
}
export function getDocirByHash(kb: string, hash: string): Promise<{ docir: DocirData | null; doc_name: string | null }> {
  return apiFetch(`/api/v1/legal/docir/by-hash/${encodeURIComponent(hash)}?kb=${encodeURIComponent(kb)}`)
}

export interface VisualNodeBrief {
  id: string
  type: string
  text_anchor?: string | null
  render_ref?: string | null
  analyzed: boolean
}

// ---------------------------------------------------------------------------
// Graph
// ---------------------------------------------------------------------------
export function listGraphNodes(kb: string, nodeType?: string): Promise<{ nodes: GraphNode[]; total: number }> {
  const q = nodeType ? `&node_type=${encodeURIComponent(nodeType)}` : ''
  return apiFetch(`/api/v1/legal/graph/nodes?kb=${encodeURIComponent(kb)}${q}`)
}
export function getGraphNode(kb: string, nodeId: string): Promise<GraphNode> {
  return apiFetch(`/api/v1/legal/graph/nodes/${encodeURIComponent(nodeId)}?kb=${encodeURIComponent(kb)}`)
}
export function getGraphImpact(kb: string, nodeId: string): Promise<{ affected: ImpactNode[]; summary: string }> {
  return apiFetch(`/api/v1/legal/graph/nodes/${encodeURIComponent(nodeId)}/impact?kb=${encodeURIComponent(kb)}`)
}
export function getGraphContradictions(kb: string): Promise<{ contradictions: { node1: GraphNode; node2: GraphNode; edges: GraphEdge[] }[] }> {
  return apiFetch(`/api/v1/legal/graph/contradictions?kb=${encodeURIComponent(kb)}`)
}

// ---------------------------------------------------------------------------
// Lifecycle
// ---------------------------------------------------------------------------
export function listLifecycle(kb: string, status?: string): Promise<{ pages: LifecycleEntry[]; total: number }> {
  const q = status ? `&status=${encodeURIComponent(status)}` : ''
  return apiFetch(`/api/v1/legal/lifecycle?kb=${encodeURIComponent(kb)}${q}`)
}
export function supersedePage(
  kb: string,
  pagePath: string,
  supersededBy: string,
  reason: string,
  triggeredBy = 'manual',
): Promise<{ status: string; superseded_by: string; version: number }> {
  return apiFetch(`/api/v1/legal/lifecycle/${encodePath(pagePath)}/supersede?kb=${encodeURIComponent(kb)}`, {
    body: { superseded_by: supersededBy, reason, triggered_by: triggeredBy },
  })
}
export interface LifecycleDetail {
  page_path: string
  version: number
  status: string
  confidence: number
  sources_count: number
  decay_rate: string
  superseded_by?: string | null
  supersede_reason?: string | null
  history: Array<{ at: string; type: string; [k: string]: unknown }>
}
export function getLifecycleDetail(kb: string, pagePath: string): Promise<LifecycleDetail> {
  return apiFetch(
    `/api/v1/legal/lifecycle/${pagePath.split('/').map(encodeURIComponent).join('/')}?kb=${encodeURIComponent(kb)}`,
  )
}

export function confirmPage(
  kb: string,
  pagePath: string,
  opts: { new_confidence?: number; add_source?: boolean; decay_rate?: string },
): Promise<{ confidence: number; sources_count: number; version: number }> {
  return apiFetch(`/api/v1/legal/lifecycle/${encodePath(pagePath)}/confidence?kb=${encodeURIComponent(kb)}`, {
    method: 'PATCH',
    body: opts,
  })
}

// ---------------------------------------------------------------------------
// Sync
// ---------------------------------------------------------------------------
export function listSyncSources(kb: string): Promise<{ source_count: number; sources: SyncSourceStat[] }> {
  return apiFetch(`/api/v1/legal/sync/sources?kb=${encodeURIComponent(kb)}`)
}
export function addSyncSource(
  kb: string,
  body: { source_id: string; path: string; name?: string; auto_sync?: boolean; sync_interval_minutes?: number },
): Promise<{ source_id: string; name: string; path: string }> {
  return apiFetch(`/api/v1/legal/sync/sources?kb=${encodeURIComponent(kb)}`, { body })
}
export function scanSyncSource(kb: string, sourceId: string): Promise<{
  new_files: string[]
  modified_files: string[]
  deleted_files: string[]
  unchanged: number
  total_scanned: number
  error?: string
}> {
  return apiFetch(`/api/v1/legal/sync/sources/${encodeURIComponent(sourceId)}/scan?kb=${encodeURIComponent(kb)}`, {
    method: 'POST',
    body: {},
  })
}
export function applySyncSource(
  kb: string,
  sourceId: string,
  full = false,
): Promise<{ ingested: { path: string; outcome: string }[]; deleted: string[]; errors: string[]; total_changed: number; error?: string }> {
  return apiFetch(`/api/v1/legal/sync/sources/${encodeURIComponent(sourceId)}/sync?kb=${encodeURIComponent(kb)}&full=${full}`, {
    method: 'POST',
    body: {},
  })
}

// ---------------------------------------------------------------------------
// Visual
// ---------------------------------------------------------------------------
export function getVisualNodesForPage(kb: string, docName: string, pageNumber: number): Promise<{ nodes: VisualNodeBrief[]; error?: string }> {
  return apiFetch(`/api/v1/legal/visual/${encodeURIComponent(docName)}/page/${pageNumber}?kb=${encodeURIComponent(kb)}`)
}

function encodePath(pagePath: string): string {
  // page_path is "concepts/x" - preserve the slash in the path segment
  return pagePath.split('/').map(encodeURIComponent).join('/')
}
