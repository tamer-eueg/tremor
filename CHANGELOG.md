# Changelog

All notable changes to Tremor will be documented here.

## Unreleased — 0.1.0 release candidate

### Added

- Request-side OpenAPI drift detection for removed endpoints/methods/parameters,
  required parameters and body fields, and parameter type changes.
- Recursive response drift detection for successful and documented error bodies.
- Local reference and composed-schema handling with cycle/depth guards.
- Automated Python integration patches for required body, query, and header inputs.
- Git-compatible patch output with apply verification and rollback on failure.
- Daily monitoring with compressed rolling baselines and structured evidence.
- Reusable GitHub Action installation and optional review-only PR workflow.
- A 27-case labeled benchmark plus historical GitHub API regression evidence.

### Security

- Watchlist URLs must use HTTPS.
- Watched files, state, reports, and patches are constrained to the repository.
- Operational failures have a distinct exit code and cannot masquerade as a clean run.

### Release blockers

- Select and publish the software license.
- Create the immutable `v0.1.0` release tag and maintained `v0` action tag.
- Replace `@master` in installation examples with `@v0` after the tag exists.
