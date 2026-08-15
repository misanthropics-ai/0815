import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it } from 'vitest'
import type { Diagnosis, Product } from '../../contracts/types'
import { Attributes, latestProductVersions, PaywalledDiagnosisReport } from './App'

const diagnosis: Diagnosis = {
  product_ref: 'test-product@v1',
  generated_at: '2026-08-15T00:00:00Z',
  overall: {
    recommendation_share: 0.28,
    consideration_share: 1,
    retrieved_rate: 0.61,
    n_simulations: 18,
    vs: { 'competitor@v1': 0.72 },
  },
  defects: [{
    defect_id: 'def_001',
    type: 'missing_attribute',
    attribute_id: 'comfort',
    severity: 'high',
    headline: 'Comfort evidence is missing',
    evidence: {
      cluster_id: 'frequent_travel',
      losing_share_in_cluster: 0.72,
      n_losses: 13,
      sample_rejection_reasons: ['No supporting comfort evidence was found.'],
      competitor_contrast: 'The competitor publishes specific comfort evidence.',
    },
    suggested_fix: 'Publish measurable comfort details.',
  }],
  winning_clusters: [],
  exec_summary: 'The product is considered but loses during final selection.',
}

const renderReport = (unlocked: boolean) => renderToStaticMarkup(
  <PaywalledDiagnosisReport
    diagnosis={diagnosis}
    fieldLabel={value => value}
    unlocked={unlocked}
    onUnlock={() => undefined}
    onDiscuss={() => undefined}
  />,
)

describe('PaywalledDiagnosisReport', () => {
  it('shows an inaccessible report preview and unlock CTA by default', () => {
    const markup = renderReport(false)

    expect(markup).toContain('Subscribe to unlock')
    expect(markup).toContain('aria-hidden="true"')
    expect(markup).toContain('inert=""')
    expect(markup).not.toContain('Full report unlocked')
  })

  it('reveals the full report after access is unlocked', () => {
    const markup = renderReport(true)

    expect(markup).toContain('Full report unlocked')
    expect(markup).toContain('Comfort evidence is missing')
    expect(markup).not.toContain('Subscribe to unlock')
    expect(markup).not.toContain('aria-hidden="true"')
    expect(markup).not.toContain('inert=""')
  })

  it('marks fallback guidance as still generating when enrichment is pending', () => {
    const pendingDiagnosis: Diagnosis = {
      ...diagnosis,
      partial: true,
      defects: [{ ...diagnosis.defects[0], content_patch: "Add a prominent 'comfort' section near the top." }],
    }
    const markup = renderToStaticMarkup(<PaywalledDiagnosisReport diagnosis={pendingDiagnosis} fieldLabel={value => value} unlocked onUnlock={() => undefined} onDiscuss={() => undefined} />)

    expect(markup).toContain('GENERATING DETAILED COPY')
    expect(markup).toContain('Product-specific copy will update automatically')
    expect(markup).not.toContain('READY-TO-PASTE COPY')
  })

  it('flags specs recovered only from product images', () => {
    const product: Product = {
      product_id: 'tv', brand: 'Example', display_name: 'Example TV', source: 'url', source_url: 'https://example.com/tv', raw_text: '', version: 1,
      attributes: [{ attribute_id: 'audio_quality', value: 'Dolby Audio, DTS:X', evidence: '[from image] Screen display', confidence: 0.8, source: 'image' }],
    }
    const markup = renderToStaticMarkup(<Attributes product={product} fieldLabel={value => value} />)

    expect(markup).toContain('Image-only specifications detected')
    expect(markup).toContain('這些規格只存在於圖片中，AI 爬蟲讀不到')
    expect(markup).toContain('📷 from image')
  })
})

describe('latestProductVersions', () => {
  it('keeps every latest product version and orders history by creation time', () => {
    const products = [
      { product_id: 'zebra', version: 1, created_at: '2026-08-15T08:00:00Z' },
      { product_id: 'alpha', version: 1, created_at: '2026-08-15T10:00:00Z' },
      { product_id: 'alpha', version: 2, created_at: '2026-08-15T11:00:00Z' },
      { product_id: 'beta', version: 1, created_at: '2026-08-15T09:00:00Z' },
    ] as Product[]

    expect(latestProductVersions(products).map(product => `${product.product_id}@v${product.version}`)).toEqual(['alpha@v2', 'beta@v1', 'zebra@v1'])
  })
})
