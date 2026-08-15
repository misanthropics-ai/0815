# Before/after experiment methodology

- Population: the first 20 intents from Contract v3's `comfort_carry` cluster.
- Repetitions: `runs=2`; 20 intents should produce 40 decisions per side.
- Control: CabinZero v1 plus the same three v1 competitors.
- Treatment: replace only CabinZero v1 with v2. Intent order, competitors, prompt/model and runs stay fixed.
- Primary metric: CabinZero recommendation share. Acceptance: delta >= 0.20.
- Report n, model/prompt version, confidence interval and exact diff.

Run: ingest the manifest, create v2 with `create_version.request.json`, then execute:

```bash
python demo/before_after/run_experiment.py --base-url http://127.0.0.1:8000 --mode live
```

If delta is below target, inspect rejection reasons and revise only truthful, sourced copy. The backend is absent from this repo snapshot, so `cached_fallback.compare.json` is an illustrative Contract fixture, not a live measurement. Replace it before making an empirical claim.
