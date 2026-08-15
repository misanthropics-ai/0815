export type Attribute = { attribute_id: string; value: string | null; evidence: string | null; confidence: number }

export const product = {
  productRef: 'cabinzero-classic-36l@v1',
  brand: 'CabinZero',
  displayName: 'CabinZero Classic 36L',
  sourceUrl: 'https://www.cabinzero.com/products/classic-36l',
  attributes: [
    { attribute_id: 'capacity', value: '36L, 44 × 30 × 20 cm', evidence: 'Sized at 44 × 30 × 20 cm', confidence: 0.97 },
    { attribute_id: 'weight', value: '760g', evidence: 'minimum weight: just 760g', confidence: 0.97 },
    { attribute_id: 'airline_compliance', value: 'Ryanair priority / easyJet large cabin bag', evidence: 'comply with most airline cabin luggage requirements', confidence: 0.92 },
    { attribute_id: 'price', value: 'EUR 79.95', evidence: 'EUR 79.95', confidence: 0.98 },
    { attribute_id: 'durability', value: '600D polyester, YKK zippers, 25y warranty', evidence: 'durable 600D polyester with YKK zippers', confidence: 0.95 },
    { attribute_id: 'organization', value: 'wide-opening main compartment', evidence: 'Wide-opening main compartment', confidence: 0.85 },
    { attribute_id: 'comfort', value: null, evidence: null, confidence: 0 },
    { attribute_id: 'back_support', value: null, evidence: null, confidence: 0 },
  ] satisfies Attribute[],
}

export const diagnosis = {
  recommendationShare: 0.34,
  competitor: { name: 'Osprey Farpoint 40', share: 0.63 },
  simulations: 120,
  defects: [
    { severity: 'high', attribute: 'back_support', headline: 'No back-support specs — losing 88% of comfort-driven comparisons', loss: 0.88, reason: '“no information available about back support or long-wear comfort”', contrast: 'Osprey specifies an AirScape mesh back panel, load-bearing hipbelt and adjustable torso fit.', fix: 'Add back-panel construction, strap padding, torso fit range and hip-belt specs.' },
    { severity: 'high', attribute: 'comfort', headline: 'Comfort claims exist nowhere on the page — AI treats comfort as unknown', loss: 0.88, reason: '“cannot assess comfort for long walking days”', contrast: 'The competitor explicitly claims all-day carry comfort with supporting hardware specs.', fix: 'Add 5h+ carry review excerpts and quantify strap padding.' },
    { severity: 'medium', attribute: 'organization', headline: 'Organization cluster loses to clamshell competitors', loss: 0.72, reason: '“only one wide compartment; no laptop sleeve mentioned”', contrast: 'Farpoint 40 lists clamshell opening and a padded laptop sleeve.', fix: 'Document internal pockets and laptop compatibility if the hardware supports it.' },
  ],
  winners: [
    { name: 'Airline compliance', share: 0.81 },
    { name: 'Budget', share: 0.74 },
    { name: 'Lightweight', share: 0.68 },
  ],
}
