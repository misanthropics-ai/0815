You are generating realistic buyer intents to test how AI shopping assistants (ChatGPT, Perplexity, Gemini) recommend products.

Category: {{category}}
Market: {{market}} | Language: {{language}}
Target brand under analysis (do NOT mention it inside intents): {{brand}}
Known competitors: {{competitors}} — a competitor name may appear ONLY in "alternatives to X" style intents.

Persona pool (sample and vary; invent adjacent personas freely):
{{personas}}

Generate for this intent cluster:
- cluster_id: {{cluster_id}}
- theme: {{cluster_label}} — {{cluster_description}}
- related attributes: {{cluster_attributes}}

Produce {{count}} distinct, realistic buyer intents for this cluster — the kind of thing a real consumer would type when shopping. Mix:
- short search-style queries ("best carry-on backpack for ryanair under €80")
- longer contextual asks ("I'm a 32-year-old accountant going to Europe for 3 weeks in October, I'll walk 20k steps a day...")
Vary persona, budget, trip type, constraints, phrasing, specificity. No two intents may be near-duplicates. Tag each intent with 1–3 attribute ids from: {{attribute_ids}}.
