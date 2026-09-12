# Security policy

## Reporting a vulnerability

Please do not open a public issue for a suspected vulnerability. Use GitHub's
[private vulnerability reporting](https://github.com/tamer-eueg/tremor/security/advisories/new)
so repository contents, exploit details, and remediation can remain private.

Include the affected revision, expected impact, reproduction steps, and any
suggested mitigation. Do not include production credentials or third-party data.

Until Tremor's first stable release, security fixes target the latest published
release candidate and `master`. Support windows will be stated with the first
stable release.

Tremor intentionally does not auto-approve or auto-merge its generated patches.
Operators should keep branch protection and their normal CI checks enabled.
