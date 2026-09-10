# Tremor benchmark results

This benchmark separates two kinds of evidence:

1. **Labeled contract mutations** measure detection precision and recall. Each case has an exact expected result.
2. **Historical regression** confirms current output still matches Tremor's reviewed GitHub API reports. It is a regression check, not independent ground truth.

## Result

- Labeled cases passed: **14/14**
- Precision: **100.0%**
- Recall: **100.0%**
- False positives: **0**
- Missed expected findings: **0**
- Historical regression: **PASS**

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

## Interpretation

Passing this suite proves the engines behave correctly for these explicitly modeled top-level OpenAPI changes and retain their reviewed output on the bundled historical GitHub data. It does **not** prove correctness for every OpenAPI feature or every API provider.

Known limits remain: deep nested response traversal, error-response schemas, cross-file references, and automated patches for every finding kind are not covered yet.

## Reproduce

```bash
python3 benchmarks/run_benchmark.py
```
