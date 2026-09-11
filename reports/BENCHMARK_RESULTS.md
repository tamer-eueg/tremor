# Tremor benchmark results

This benchmark separates two kinds of evidence:

1. **Labeled contract mutations** measure detection precision and recall. Each case has an exact expected result.
2. **Historical regression** confirms current output retains every finding in Tremor's reviewed GitHub API reports. New detector capabilities may produce additional candidates; those are not presented as independently verified ground truth.

## Result

- Labeled cases passed: **27/27**
- Precision: **100.0%**
- Recall: **100.0%**
- False positives: **0**
- Missed expected findings: **0**
- Historical regression: **PASS**
- Reviewed historical findings lost: **0**
- Additional historical response candidates from nested traversal: **117**

## Labeled cases

| Case | Result | False positives | Misses |
|---|---:|---:|---:|
| unchanged contract | PASS | 0 | 0 |
| endpoint removed | PASS | 0 | 0 |
| method removed | PASS | 0 | 0 |
| parameter removed | PASS | 0 | 0 |
| parameter becomes required | PASS | 0 | 0 |
| parameter type changes | PASS | 0 | 0 |
| required parameter added | PASS | 0 | 0 |
| optional parameter added safely | PASS | 0 | 0 |
| required request field added | PASS | 0 | 0 |
| response field removed | PASS | 0 | 0 |
| response field type changes | PASS | 0 | 0 |
| response field added safely | PASS | 0 | 0 |
| endpoint added safely | PASS | 0 | 0 |
| anyOf wrapper does not invent removals | PASS | 0 | 0 |
| path-level parameter becomes required | PASS | 0 | 0 |
| operation parameter safely overrides path parameter | PASS | 0 | 0 |
| required field added through schema reference | PASS | 0 | 0 |
| required field added through requestBody reference | PASS | 0 | 0 |
| required field added through allOf | PASS | 0 | 0 |
| branch-only anyOf requirement does not create false alert | PASS | 0 | 0 |
| nested response field removed | PASS | 0 | 0 |
| nested response field type changes | PASS | 0 | 0 |
| nested response change through local reference | PASS | 0 | 0 |
| error response field removed | PASS | 0 | 0 |
| error response field type changes | PASS | 0 | 0 |
| error response field added safely | PASS | 0 | 0 |
| new error status does not create speculative alert | PASS | 0 | 0 |

## Interpretation

Passing this suite proves the engines behave correctly for these explicitly modeled OpenAPI changes and retain every reviewed finding on the bundled historical GitHub data. It does **not** prove correctness for every OpenAPI feature or independently validate the additional historical candidates found by deeper traversal.

Known limits remain: wildcard/default error responses, status-code-set classification, cross-file references, and automated patches for every finding kind are not covered yet.

## Reproduce

```bash
python3 benchmarks/run_benchmark.py
```
