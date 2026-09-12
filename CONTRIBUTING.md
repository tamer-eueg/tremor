# Contributing to Tremor

Tremor is currently preparing its first public release. Small, focused changes
with reproducible tests are welcome after a license is selected.

## Development checks

Tremor uses Python 3.12 and the standard library; it has no runtime package
dependencies.

```bash
python3 -m unittest discover -s tests -v
python3 benchmarks/run_benchmark.py
python3 -m py_compile src/*.py tests/*.py benchmarks/*.py
```

The benchmark must retain every reviewed historical finding and all labeled
cases must pass. New detector behavior should add a positive case and, where
false alerts are possible, a negative case.

## Pull requests

- Keep changes within one clear concern.
- Explain the contract behavior being added or corrected.
- Do not weaken review-only delivery or add automatic merge behavior.
- Do not include API credentials, customer code, or private specifications.
- Update the README and installation guide when behavior changes.

Until a project license is selected and published, maintainers will defer
accepting external code contributions. Issues and reproducible bug reports can
still be submitted without contributing code.
