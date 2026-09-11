# Install Tremor in a GitHub repository

Tremor currently installs as a GitHub Action. It needs no server, database,
account, API key, or customer outreach. The first run stores an API baseline;
later runs compare the provider's current OpenAPI document with that baseline.

## 1. Add the watchlist

Copy `examples/watchlist.example.json` to `tremor-watchlist.json` in the root of
the repository that contains the integration. Replace:

- `name` with a short unique provider name.
- `spec_url` with the provider's public HTTPS OpenAPI JSON URL.
- `watched_files` with repository-relative Python files that call the API.

Each configured URL must use HTTPS. Watched files cannot be absolute paths or
escape the repository. Tremor validates the full configuration before fetching
or writing anything.

## 2. Add the workflow

Copy `examples/tremor-watch.workflow.yml` to
`.github/workflows/tremor-watch.yml` in the integration repository.

The workflow deliberately grants only `contents: write`: it needs that access
to preserve the rolling baseline and reports. Tremor does not auto-merge or
modify the watched source file. Generated candidate patches are stored under
`.tremor/runs/patches/` for review.

For production, replace `@master` in the example with a released tag or full
commit SHA. A stable release tag is part of Tremor's public-release milestone.

## 3. Enable workflow writes

In the integration repository, open **Settings → Actions → General → Workflow
permissions**, select **Read and write permissions**, and save.

## 4. Establish the baseline

Open **Actions → Tremor watch → Run workflow**. A green first run creates and
commits `.tremor/state/`. It cannot report drift yet because there is no older
baseline.

## 5. Read later results

On later runs:

- Green means no breaking contract change was detected.
- Red means Tremor detected breaking drift or could not complete the check.
- Structured findings are stored under `.tremor/runs/`.
- Candidate patches are stored under `.tremor/runs/patches/`; review them before
  applying them.

Tremor never auto-merges generated changes.

## Optional: open review pull requests

After the basic installation is stable, replace the workflow with
`examples/tremor-review-pr.workflow.yml`. This mode:

1. Detects drift and generates patches.
2. Applies only newly generated `.patch` files inside the temporary Actions runner.
3. Verifies each patch with Git before applying it; if a later patch fails, earlier
   applications are rolled back.
4. Commits the baseline/evidence to the default branch.
5. Pushes source changes to a unique `tremor/api-drift-*` branch and opens a pull request.

The workflow uses only GitHub's checkout/setup actions and the preinstalled GitHub CLI.
It grants `pull-requests: write` solely to open the review PR. It contains no approval or
merge command, and Tremor's Action cannot merge a PR.

In **Settings → Actions → General → Workflow permissions**, the repository owner must also
enable GitHub Actions to create pull requests. Keep required reviews and branch protection
enabled: Tremor's PR should pass the repository's normal tests and human review.

If Tremor detects breaking drift but cannot make a safe patch, it stores the report and
opens no empty PR. Operational failures still fail the workflow; `fail-on-breaking: false`
only converts confirmed drift into the review-PR path.
