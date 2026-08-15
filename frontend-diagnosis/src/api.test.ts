import { describe, expect, it } from 'vitest'
import type { DiagnosisPending } from '../../contracts/types'
import { ApiFailure, buildCreateProductRequest, interpretDiagnosisResponse } from './api'

describe('buildCreateProductRequest', () => {
  it('sends the selected category with URL ingestion', () => {
    expect(buildCreateProductRequest({
      mode: 'url',
      value: 'https://www.cabinzero.com/products/classic-36l',
      category: ' travel backpack ',
      brand: '',
      displayName: '',
    })).toEqual({
      source: 'url',
      source_url: 'https://www.cabinzero.com/products/classic-36l',
      category: 'travel backpack',
    })
  })
})

describe('interpretDiagnosisResponse', () => {
  it('keeps polling a running diagnosis', () => {
    const state: DiagnosisPending = { status: 'running', detail: 'working' }

    expect(interpretDiagnosisResponse(202, state)).toEqual({ pending: true, state })
  })

  it('surfaces needs_competitors immediately', () => {
    const state: DiagnosisPending = {
      status: 'needs_competitors',
      category: 'travel bags and luggage',
      detail: 'ingest another brand',
    }

    expect(() => interpretDiagnosisResponse(202, state)).toThrow(ApiFailure)
    expect(() => interpretDiagnosisResponse(202, state)).toThrow(
      'Diagnosis needs a product from another brand in the same category',
    )
  })
})
