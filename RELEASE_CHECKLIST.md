# Tremor 0.1.0 release checklist

## Automated gates

- [x] Unit and security tests pass.
- [x] All 27 labeled benchmark cases pass.
- [x] Historical regression retains every reviewed finding.
- [x] GitHub Actions quality gate runs on pushes and pull requests.
- [x] Runtime uses only Python's standard library.
- [x] Self-service Action and review-only PR examples are documented.
- [x] Security reporting policy is published.
- [x] Changelog and contribution guide are published.

## Owner decisions required before public release

- [ ] Choose a software license. Apache-2.0 is recommended for an open-source
  developer tool because it includes an explicit patent grant; MIT is shorter
  and simpler. This is a legal/business decision for EUEG OÜ.
- [ ] Confirm the product may be publicly presented as an EUEG OÜ open-source project.
- [ ] Approve making the privately deployed product site public.

## Release operations after approval

- [ ] Add the selected `LICENSE` file and remove the temporary contribution restriction.
- [ ] Replace installation examples from `@master` to `@v0`.
- [ ] Run the complete quality gate on the release commit.
- [ ] Create immutable tag `v0.1.0` and GitHub release notes from `CHANGELOG.md`.
- [ ] Create/update major action tag `v0` to the exact `v0.1.0` commit.
- [ ] Test a first-run baseline in a clean repository using `@v0`.
- [ ] Test a review-only patch PR in a protected clean repository.
- [ ] Publish the product site only after both installation tests pass.

No billing is required for the free 0.1.0 release.
