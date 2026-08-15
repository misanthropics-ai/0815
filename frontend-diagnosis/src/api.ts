import type { CompareResult, CreateProductRequest, DebateMessage, DebateSession, Diagnosis, DiagnosisPending, Product, ProductRef, Taxonomy } from '../../contracts/types'
import productFixture from '../../backend/mock_fixtures/response.post_products.manual.json'
import diagnosisFixture from '../../backend/mock_fixtures/response.diagnosis.json'
import sessionFixture from '../../backend/mock_fixtures/response.get_debate_session.json'
import compareFixture from '../../backend/mock_fixtures/response.metrics_compare.json'
import taxonomyFixture from '../../backend/mock_fixtures/taxonomy.json'

const API = import.meta.env.VITE_API_BASE?.replace(/\/$/, '')
const pause = (ms: number) => new Promise(resolve => window.setTimeout(resolve, ms))
export const isMock = !API
export class ApiFailure extends Error {
  constructor(message: string, readonly code?: string, readonly hint?: string) { super(message) }
}

export function buildCreateProductRequest(input: {
  mode: 'url' | 'manual_prototype'
  value: string
  category: string
  brand: string
  displayName: string
}): CreateProductRequest {
  const category = input.category.trim() || undefined
  return input.mode === 'url'
    ? { source: 'url', source_url: input.value, category }
    : {
        source: 'manual_prototype',
        brand: input.brand || 'My brand',
        display_name: input.displayName || undefined,
        raw_text: input.value,
        category,
      }
}

async function request<T>(path: string, init?: RequestInit): Promise<{ status: number; data: T }> {
  const response = await fetch(`${API}${path}`, { ...init, headers: { 'content-type': 'application/json', ...init?.headers } })
  const data = await response.json()
  if (!response.ok && response.status !== 202) {
    throw new ApiFailure(data.error?.message ?? 'The service could not complete this request.', data.error?.code, data.error?.hint)
  }
  return { status: response.status, data }
}

export async function createProduct(input: CreateProductRequest): Promise<Product> {
  if (!API) { await pause(800); return productFixture as Product }
  return (await request<Product>('/products', { method: 'POST', body: JSON.stringify(input) })).data
}

export async function listProducts(): Promise<Product[]> {
  if (!API) return [productFixture as Product]
  return (await request<{ products: Product[] }>('/products')).data.products
}

export async function getTaxonomy(category?: string): Promise<Taxonomy> {
  if (!API) return taxonomyFixture as Taxonomy
  const query = category?.trim() ? `?category=${encodeURIComponent(category.trim())}` : ''
  return (await request<Taxonomy>(`/taxonomy${query}`)).data
}

export function interpretDiagnosisResponse(status: number, data: Diagnosis | DiagnosisPending): { pending: boolean; data?: Diagnosis; state?: DiagnosisPending } {
  if (status !== 202) return { pending: false, data: data as Diagnosis }
  const state = data as DiagnosisPending
  if (state.status === 'needs_competitors') {
    const category = state.category ? ` for “${state.category}”` : ''
    throw new ApiFailure(`Diagnosis needs a product from another brand in the same category${category}. Add a competitor product, then retry.`, state.status, state.detail)
  }
  if (state.status === 'failed') {
    throw new ApiFailure(state.detail ?? 'Diagnosis failed. Please retry.', state.status)
  }
  return { pending: true, state }
}

export async function getDiagnosis(ref: ProductRef): Promise<{ pending: boolean; data?: Diagnosis; state?: DiagnosisPending }> {
  if (!API) { await pause(500); return { pending: false, data: diagnosisFixture as Diagnosis } }
  const result = await request<Diagnosis | DiagnosisPending>(`/products/${encodeURIComponent(ref)}/diagnosis`)
  return interpretDiagnosisResponse(result.status, result.data)
}

export async function createDebate(ref: ProductRef, focus?: string): Promise<DebateSession> {
  if (!API) return { session_id: 'mock-debate', product_ref: ref, messages: [] }
  const created = await request<{ session_id: string; product_ref: ProductRef }>('/debate/sessions', { method: 'POST', body: JSON.stringify({ product_ref: ref, focus_defect_id: focus }) })
  return { ...created.data, messages: [] }
}

export async function getCompare(url: string): Promise<{ pending: boolean; data?: CompareResult }> {
  if (!API) { await pause(700); return { pending: false, data: compareFixture as CompareResult } }
  const result = await request<CompareResult>(url)
  return result.status === 202 ? { pending: true } : { pending: false, data: result.data }
}

export async function streamDebate(sessionId: string, text: string, onToken: (text: string) => void, onAction: (action: any) => void): Promise<void> {
  if (!API) {
    const reply = '這正是問題的核心：產品有優點，但頁面沒寫，AI 就當它不存在。把可驗證的背板、肩帶與測試資訊寫上頁面，再重跑模擬，才能證明推薦率是否改變。'
    for (const word of reply.match(/.{1,7}/g) ?? []) { await pause(45); onToken(word) }
    onAction({ type: 'create_version_and_rerun', status: 'started', params: { additions: ['Ventilated mesh back panel and memory foam shoulder straps.'], cluster_id: 'weight_minimal' }, base_ref: 'cabinzero-classic-36l@v1', new_ref: 'cabinzero-classic-36l@v2', compare_url: '/metrics/compare' })
    return
  }
  const response = await fetch(`${API}/debate/sessions/${sessionId}/messages`, { method: 'POST', headers: { 'content-type': 'application/json', accept: 'text/event-stream' }, body: JSON.stringify({ text }) })
  if (!response.ok || !response.body) throw new ApiFailure('The debate stream could not be opened.')
  const reader = response.body.getReader(), decoder = new TextDecoder(); let buffer = ''
  while (true) {
    const { value, done } = await reader.read(); if (done) return
    buffer += decoder.decode(value, { stream: true }); let boundary
    while ((boundary = buffer.indexOf('\n\n')) >= 0) {
      const block = buffer.slice(0, boundary); buffer = buffer.slice(boundary + 2)
      const event = block.match(/^event: (.+)$/m)?.[1]; const raw = block.match(/^data: (.+)$/m)?.[1]
      if (!event || !raw) continue; const data = JSON.parse(raw)
      if (event === 'token') onToken(data.text); else if (event === 'action') onAction(data.action); else if (event === 'error') throw new ApiFailure(data.message)
    }
  }
}

export const sampleSession = sessionFixture as DebateSession
