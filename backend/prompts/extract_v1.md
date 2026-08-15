Extract product attributes from this product page text. Use ONLY information explicitly present in the text — no outside knowledge, no guessing.

Product hint: {{display_name}} (brand hint: {{brand}})

## Attribute taxonomy
{{taxonomy_block}}

## Page text
<page_text>
{{raw_text}}
</page_text>

For EVERY taxonomy attribute except "other", return:
- attribute_id
- value: concise factual value stated by the page (e.g. "760 g", "fits 55x40x20 Ryanair limit"), or null if the page does not state it
- evidence: short verbatim quote from the page supporting the value, or null
- confidence: 0..1 (use 0.0 when value is null)

value=null is important diagnostic signal ("the page doesn't say it") — never fill in what the page doesn't state. Also return: brand, display_name (from text if clearly stated, else keep the hints), and product_id: a lowercase hyphenated slug like "cabinzero-classic-36l".
