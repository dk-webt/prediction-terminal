# Semantic Matching Test Harness

Test harness for evaluating and iterating on the semantic matching algorithm. Located at `test_matching.py`.

## Setup

```bash
# Install dependencies (if not already done)
python3 -m pip install -r requirements.txt --break-system-packages
```

## Workflow

### 1. Snapshot live events

Fetches events from both Polymarket and Kalshi and saves them as a JSON fixture. This avoids hitting the APIs on every test run.

```bash
python3 test_matching.py snapshot --limit 200
```

- Saves to `test_fixtures/pm_events.json` and `test_fixtures/ks_events.json`
- Run this once, then reuse the fixture across all tests
- Re-run when you want fresh data (e.g. new events appeared)
- `--limit` controls how many events to fetch per platform (200+ recommended for meaningful overlap)

### 2. Inspect raw scores (`topk`)

Shows the top-N pairs from the full similarity matrix with a breakdown of every scoring component. No 1-to-1 assignment is applied — this is the raw scoring before greedy picks.

```bash
python3 test_matching.py topk --k 30
```

Output columns:
- **Comp** — composite score (weighted geometric mean of all signals)
- **Cos** — raw cosine similarity from Gemini embeddings
- **Cat** — category compatibility (1.0 = same, 0.5 = one is "Other", 0.0 = different)
- **Bkt** — bracket count similarity (1.0 = identical count, decays toward 0.5)
- **Date** — date proximity (1.0 = same date, decays with distance, floor 0.6)
- **Label** — your ground truth label if graded (checkmark = correct, X = wrong)

Use this to:
- See which scoring component is dragging good matches below threshold
- Check if a PM event has multiple strong KS candidates
- Tune composite weights and thresholds

### 3. Run a single matcher

Runs one matcher and shows all matches sorted by score.

```bash
# Run V2 (default)
python3 test_matching.py run --matcher v2

# Run V1 for comparison
python3 test_matching.py run --matcher v1

# Custom thresholds
python3 test_matching.py run --matcher v2 --event-min 0.80 --market-min 0.85

# Include sub-market matches in output
python3 test_matching.py run --matcher v2 --show-markets
```

### 4. Compare V1 vs V2

Runs both matchers on the same fixture and shows overlap, differences, and score comparisons.

```bash
python3 test_matching.py compare
python3 test_matching.py compare --event-min 0.80
```

Output includes:
- Count of matches shared by both, V1-only, and V2-only
- If labels exist: accuracy breakdown per matcher
- Full list of differing matches with scores
- Score comparison for shared matches (which matcher scores them higher)

### 5. Grade matches (build ground truth)

Walks through each match interactively and asks you to label it as correct or wrong. Labels are saved to `test_fixtures/labels.json`.

```bash
# Grade V2 matches
python3 test_matching.py grade

# Grade V1 matches
python3 test_matching.py grade --matcher v1

# Re-grade previously labeled pairs
python3 test_matching.py grade --regrade
```

For each match you see:
```
  3/32 [0.8322]
    PM: Presidential Election Winner 2028
    KS: 2028 U.S. Presidential Election winner?
    PM brackets: Person R, Person AZ, Person BY
    KS brackets: 2028 U.S. Presidential Election winner?
    Grade [y/n/skip/q]:
```

Input options:
- `y` — correct match (same real-world event)
- `n` — wrong match (different events)
- Enter — skip (don't label)
- `q` — quit and save

Labels persist across sessions. You only need to grade each pair once.

### 6. Score matchers against labels

Computes precision, recall, and F1 for each matcher against your ground truth labels.

```bash
python3 test_matching.py score
python3 test_matching.py score --event-min 0.80
```

Output:
```
Labels: 12 correct, 8 wrong

  V1: 10 matches | TP=8 FP=2 FN=4 | P=0.80 R=0.67 F1=0.73
  V2: 32 matches | TP=12 FP=20 FN=0 | P=0.38 R=1.00 F1=0.55
```

- **TP** (true positive) — matched and labeled correct
- **FP** (false positive) — matched but labeled wrong
- **FN** (false negative) — not matched but labeled correct
- **P** (precision) — what fraction of matches are correct
- **R** (recall) — what fraction of correct pairs were found
- **F1** — harmonic mean of precision and recall

## Iteration Workflow

1. `snapshot` — fetch events (once)
2. `grade` — label 20-30 matches as correct/wrong (once per fixture)
3. Edit `matchers/v2.py` — change embedding templates, weights, thresholds
4. `score` — measure improvement
5. `topk` — diagnose specific scoring issues
6. Repeat 3-5

## Files

```
test_matching.py              — the test harness script
test_fixtures/                — gitignored, generated per-machine
  pm_events.json              — snapshotted Polymarket events
  ks_events.json              — snapshotted Kalshi events
  labels.json                 — your ground truth labels (persists across runs)
```
