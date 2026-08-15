# P6 demo deliverables

This directory contains the real-product evidence set, controlled CabinZero v1/v2 experiment, timed demo script, fallback fixture, rehearsal log, and five-slide pitch deck.

## Validate

```bash
python contracts/check_contract.py
python demo/validate_demo_data.py
```

## Run the controlled experiment

Ingest the four URLs in `real_products/ingest_manifest.json`, create CabinZero v2 with `before_after/create_version.request.json`, then run:

```bash
python demo/before_after/run_experiment.py \
  --base-url http://127.0.0.1:8000 \
  --mode live
```

The experiment uses 20 fixed `comfort_carry` intents × 2 runs = 40 decisions per side. It fails closed if either side does not produce exactly 40 decisions.

`before_after/cached_fallback.compare.json` is an illustrative UI fallback, not a live measurement. Replace it with `latest.compare.json` before making an empirical claim.
