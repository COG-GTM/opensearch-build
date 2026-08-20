# Jenkins → GitHub Actions: full-estate migration plan and risk analysis

This document is the planning deliverable for migrating the **entire** Jenkins CI estate of this
repository to GitHub Actions. It covers the 36 active declarative pipelines under `jenkins/`
(everything except `jenkins/legacy/`), the shared library
[`opensearch-project/opensearch-build-libraries`](https://github.com/opensearch-project/opensearch-build-libraries)
they load, the Groovy regression suite under `tests/jenkins/`, and the lessons from the pilot
migration in [PR #1](https://github.com/COG-GTM/opensearch-build/pull/1)
(`distribution-build` + `integ-test`, 24 composite actions, `docs/jenkins-to-actions.md`).

Every claim below is grounded in a file path. Line numbers refer to commit `4372bf7` of this fork
(the `main` HEAD this plan was written against) and to the shared-library tags
`jenkins@12.0.0`–`13.7.1` as pinned by the pipelines; resolve citations against that commit, since
they will drift as the Jenkinsfiles and manifests roll forward. Where something is an inference
rather than a verified fact, it is explicitly marked **(inferred)**.

GitHub-platform limits quoted in this document (6 h hosted-job cap, 5-day self-hosted job cap,
35-day workflow-run cap, 256-job matrix cap, 10 `workflow_dispatch` inputs, environment-approval
and schedule-deactivation windows, retention ceilings) are taken from GitHub's published
documentation as of the writing date; they are platform policy, not repo facts, and must be
re-verified against [GitHub's usage-limit docs](https://docs.github.com/en/actions/reference/limits)
at implementation time.

Scope notes:

* `jenkins/legacy/` (including `jenkins/legacy/build.ci.backups/`) is treated as dead code and is
  **not** in scope. Confirm with the customer before deleting it (Open decision D8).
* The repo already runs 26 GitHub Actions workflows under `.github/workflows/` (linting, Python
  tests, the Groovy regression suite via `groovy-tests.yml`, release-issue automation). These are
  unaffected; the migration adds to them.
* PR #1 is open and unmerged. This plan references it as the pilot; it does not build on top of it.

---

## 1. Pipeline inventory

36 active `*.jenkinsfile` files. "What it does" comes from the `// @description:` header each file
carries at lines 10–11. "Trigger today" is what is declared in the file; pipelines with no
`triggers {}` block are started manually or by an upstream `build job:` call. Complexity: **S**
(direct port), **M** (needs restructuring), **L** (semantic gaps, see §4). Fan-out of
shared-library steps is the list of `vars/*.groovy` steps the file calls directly
(`postCleanup` is called by 34 of 36 files and is omitted from the per-row lists).

Suffix note: the `-lf` suffix on 11 filenames matches the pipelines that reference
`opensearchorg/opensearchstaging/...` images pulled through `public.ecr.aws`; **(inferred)** it
marks jobs already moved to the Linux Foundation Jenkins instance. It does not change the migration
approach.

### `jenkins/opensearch/` — 12 pipelines

| Pipeline | Size | What it does | Trigger today | Target GHA trigger | Cx | Shared-library steps |
| --- | ---: | --- | --- | --- | :-: | --- |
| `distribution-build.jenkinsfile` | 58 KB | Builds the OpenSearch distribution from an input manifest across 7 platform/dist stages; publishes results, autocuts issues, triggers downstream tests | upstream `check-for-build` | `workflow_dispatch` + `workflow_call` (pilot exists) | L | buildArchive, buildAssembleUpload, archiveAssembleUpload, buildDockerImage, buildMessage, detectDockerAgent, getLogsForStage, publishDistributionBuildResults, updateBuildFailureIssues, uploadIndexFile |
| `integ-test.jenkinsfile` | 20 KB | Distribution-level integration tests for all OpenSearch plugins, dynamic per-component parallel fan-out (`:209` "Using scripted pipelines to trigger dynamic parallel stages") | upstream `distribution-build` | `workflow_dispatch` + `workflow_call` (pilot exists) | L | detectTestDockerAgent, downloadBuildManifest, downloadFromS3, runIntegTestScript, uploadTestResults, createUploadTestReportManifest, publishIntegTestResults |
| `bwc-test.jenkinsfile` | 5 KB | Backward-compatibility tests for the OpenSearch distribution | upstream | `workflow_call` + `workflow_dispatch` | S | detectTestDockerAgent, downloadBuildManifest, runBwcTestScript, uploadTestResults |
| `smoke-test-lf.jenkinsfile` | 8 KB | Smoke tests on a built distribution | upstream | `workflow_call` + `workflow_dispatch` | S | detectTestDockerAgent, downloadBuildManifest, runSmokeTestScript, createUploadTestReportManifest, publishSmokeTestResults, uploadTestResults |
| `feature-build.jenkinsfile` | 14 KB | Distribution build for feature branches | `parameterizedCron` (1 entry) | `schedule` + repo-variable param table (§4-F1) | M | buildAssembleUpload, detectDockerAgent, getLogsForStage, uploadIndexFile |
| `publish-min-snapshots.jenkinsfile` | 10 KB | Publishes min (core-only) snapshots to S3 | `parameterizedCron` (4 entries) | `schedule` + param table | M | buildManifest, detectDockerAgent, uploadMinSnapshotsToS3 |
| `benchmark-test.jenkinsfile` | 81 KB | Nightly OpenSearch Benchmark runs on ephemeral CDK clusters | `parameterizedCron` — **96 cron entries**, each a full parameter set (`:35` onward) | `schedule` + config-matrix files (§4-F1) | L | downloadBuildManifest, publishNotification, runBenchmarkTestScript |
| `benchmark-test-vectorsearch.jenkinsfile` | 90 KB | Vector-search benchmark runs | `parameterizedCron` (56 entries) | same as above | L | downloadBuildManifest, publishNotification, runBenchmarkTestScript |
| `benchmark-test-datafusion.jenkinsfile` | 37 KB | Datafusion benchmark runs | `parameterizedCron` (12 entries) | same | L | downloadBuildManifest, publishNotification, runBenchmarkTestScript |
| `benchmark-test-endpoint.jenkinsfile` | 21 KB | Benchmarks against an existing cluster endpoint | `parameterizedCron` (21 entries) | same | M | runBenchmarkTestScript |
| `benchmark-pull-request.jenkinsfile` | 24 KB | Benchmarks a PR artifact; comments results back on the PR | **`GenericTrigger` webhook** (`:178`) + `parameterizedCron` | `repository_dispatch` (§4-F9) | L | getCompareBenchmarkIds, runBenchmarkTestScript |
| `benchmark-compare.jenkinsfile` | 5 KB | Compares two benchmark runs | downstream of benchmark-pull-request (`build job: 'benchmark-compare'`) | `workflow_call` | S | runBenchmarkTestScript |

### `jenkins/opensearch-dashboards/` — 3 pipelines

| Pipeline | Size | What it does | Trigger today | Target GHA trigger | Cx | Shared-library steps |
| --- | ---: | --- | --- | --- | :-: | --- |
| `distribution-build.jenkinsfile` | 58 KB | OSD distribution build (mirror of the OpenSearch one) | upstream `check-for-build` | `workflow_dispatch` + `workflow_call` | L | same set as the OpenSearch build |
| `integ-test.jenkinsfile` | 25 KB | OSD distribution-level integ tests, dynamic fan-out + `stash`/`unstash` (`:195`) | upstream | `workflow_dispatch` + `workflow_call` | L | detectTestDockerAgent, downloadBuildManifest, downloadFromS3, runIntegTestScriptForOSD, uploadTestResults, createUploadTestReportManifest, publishIntegTestResults |
| `bwc-test.jenkinsfile` | 6 KB | OSD BWC tests | upstream | `workflow_call` | S | detectTestDockerAgent, downloadBuildManifest, runBwcTestScript, uploadTestResults, publishNotification |

### `jenkins/release-workflows/` — 12 pipelines

| Pipeline | Size | What it does | Trigger today | Target GHA trigger | Cx | Shared-library steps |
| --- | ---: | --- | --- | --- | :-: | --- |
| `release-promotion.jenkinsfile` | 47 KB | Central RC→GA promotion orchestrator: **24 `build job:` calls**, almost all `wait: true` (`:92–:242` and onward) | manual | `workflow_dispatch` orchestrator calling reusable workflows (§4-F4) | L | (orchestration only) |
| `release-chores.jenkinsfile` | 12 KB | Release-owner assignment, doc checks, release-notes checks, RC builds, code-coverage, integ-results overview | manual | `workflow_dispatch`, one job per chore | M | addRcDetailsComment, buildRC, checkCodeCoverage, checkDocumentationIssues, checkDocumentationPullRequests, checkIntegTestResultsOverview, checkReleaseIssues, checkReleaseNotes, checkRequestAssignReleaseOwners |
| `release-manifest-commit-lock-lf.jenkinsfile` | 16 KB | Pins the release manifest to specific commit IDs; opens a manifest-update PR | manual + downstream of `release-tag` | `workflow_dispatch` | M | (uses repo scripts + gh) |
| `release-notes-generate.jenkinsfile` | 14 KB | Generates release notes (uses `stash`, `:` two occurrences; AWS Bedrock LLM per repo knowledge) | downstream of `release-chores` | `workflow_call` | M | (repo scripts) |
| `release-notes-check-lf.jenkinsfile` | 9 KB | Checks/compiles release notes for all components | manual | `workflow_dispatch` | S | (repo scripts) |
| `promote-artifacts.jenkinsfile` | 3 KB | Staging→production artifact promotion | downstream of `release-promotion` | `workflow_call` behind an `environment` gate (§4-F3) | M | createSha512Checksums, createSignatureFiles, promoteArtifacts |
| `promote-repos.jenkinsfile` | 3 KB | Promotes yum/apt repos | downstream | `workflow_call` behind an `environment` gate | M | promoteRepos |
| `promote-docker-ecr-lf.jenkinsfile` | 4 KB | Staging→production Docker/ECR promotion | downstream | `workflow_call` behind an `environment` gate | M | promoteContainer |
| `publish-to-maven-lf.jenkinsfile` | 3 KB | Publishes to Maven Central | downstream | `workflow_call` behind an `environment` gate | M | downloadFromS3, publishToMaven |
| `release-branch.jenkinsfile` | 5 KB | Creates release branches across component repos | manual | `workflow_dispatch` | S | downloadBuildManifest |
| `release-tag.jenkinsfile` | 3 KB | Tags all components; triggers `release-manifest-commit-lock` | manual | `workflow_dispatch` | S | createReleaseTag |
| `release-schedule.jenkinsfile` | 3 KB | Daily: reads opensearch.org/releases.html, registers the schedule | `cron('H 8 * * *')` | `schedule` | S | registerReleaseSchedule |
| `release-schedule` note | | the only pipeline using plain `cron()` instead of `parameterizedCron` | | | | |

### `jenkins/docker/`, `jenkins/packer/`, `jenkins/gradle/`, top level — 9 pipelines

| Pipeline | Size | What it does | Trigger today | Target GHA trigger | Cx | Shared-library steps |
| --- | ---: | --- | --- | --- | :-: | --- |
| `docker/docker-build-lf.jenkinsfile` | 5 KB | Builds/publishes Docker images; **Windows docker agent** with named-pipe mount (`:20`) | downstream of distribution builds | `workflow_dispatch` + `workflow_call` | M | (docker CLI) |
| `docker/docker-copy-lf.jenkinsfile` | 4 KB | Copies images between registries (docker.sock mount `-u root -v /var/run/docker.sock`) | downstream | `workflow_call` | S | copyContainer |
| `docker/docker-re-release-lf.jenkinsfile` | 2 KB | Re-releases images on updated base images | `parameterizedCron` (4 entries) | `schedule` | S | patchDockerImage |
| `docker/docker-scan-lf.jenkinsfile` | 3 KB | Trivy-style scan of an image | manual/downstream | `workflow_dispatch` | S | scanDockerImage |
| `packer/packer-build-lf.jenkinsfile` | 5 KB | Builds **Jenkins agent AMIs** with Packer | manual | see Open decision D6 — this job may be obsolete after migration (runner AMIs instead) | S | (packer CLI) |
| `gradle/gradle-check-flaky-test-issue-creation.jenkinsfile` | 2 KB | Detects flaky gradle-check tests, cuts issues | `parameterizedCron` | `schedule` | M (§4-F10) | gradleCheckFlakyTestDetector |
| `check-for-build.jenkinsfile` | 6 KB | Hourly poll: compares manifest SHAs, triggers distribution builds; uses `lock(resource:..., skipIfLocked: true)` (`:104`) and `build job: TARGET_JOB_NAME` (`:116`) | `parameterizedCron` (3 entries, `:27–33`) | `schedule` + `concurrency` (§4-F2) | M | buildUploadManifestSHA, detectDockerAgent, getManifestSHA |
| `integ-test-notification.jenkinsfile` | 2 KB | Cuts GitHub issues for failed integ-test components | downstream (`build job: 'integ-test-notification', wait: false`, `integ-test.jenkinsfile:330–345`) | `workflow_call` or merged into the integ-test workflow | S | updateIntegTestFailureIssues |
| `validate-artifacts/validate-artifacts-lf.jenkinsfile` | 16 KB | Validates distribution artifacts on tar/rpm/deb/yum/zip across x64/arm64/Windows; **scripted `node()` allocation inside loops** (`:216`, `:243`) and systemd-entrypoint containers (`:28–32`) | downstream (`build job: 'distribution-validation'` from 4 pipelines) | `workflow_call` with a generated matrix | L | validateArtifacts |

Trigger-model summary (verified by grepping for `triggers` blocks, including the no-space
`triggers{` in `benchmark-test-endpoint`): 11 pipelines declare a `triggers {}` block, all with
cron-style entries; one of those (`benchmark-pull-request`) additionally has a `GenericTrigger`
webhook. The remaining 25 are manual or upstream-triggered. There are
**no** `pollSCM`, no multibranch, and no `input()` approval steps anywhere under active `jenkins/`
(grep for `input(` returns only legacy files — the human gates in this estate are "a human clicks
Build with Parameters", not `input`).

---

## 2. Shared-library analysis (`opensearch-build-libraries`)

89 `vars/*.groovy` steps. 52 are called directly by at least one active pipeline in this repo; the
other 37 (e.g. `publishToNpm`, `publishToCrates`, `runGradleCheck`, `standardReleasePipeline*`) are
consumed by *other* OpenSearch repos' Jenkinsfiles — they matter for the ecosystem but not for this
repo's cut-over (Open decision D7). Fan-in below = number of active pipelines in this repo calling
the step directly (steps also call each other, e.g. `buildAssembleUpload` → `buildManifest` +
`assembleManifest` + `uploadArtifacts`, so effective fan-in is higher).

### Group A — becomes a composite action (stateless step, shell + small logic)

| Step | Fan-in | Notes |
| --- | ---: | --- |
| `postCleanup` | 34 | **Delete, don't port.** GHA runners/job containers are ephemeral (pilot reached the same conclusion, PR #1 doc §2). On *self-hosted* runners (§6) a cleanup step IS still needed — the pilot's "not needed" claim only holds for hosted runners. |
| `downloadBuildManifest` | 9 | curl + validation; trivial composite (exists in pilot). |
| `runBenchmarkTestScript` | 6 | 213 lines, 5 credential bindings; composite + OIDC. Highest-leverage *unported* step: unlocks all 6 benchmark pipelines. |
| `detectDockerAgent` / `detectTestDockerAgent` | 5+5 | Ported in pilot as `detect-docker-agent`; keep the pilot's Python reimplementation (`manifest_paths.py`) as the single source instead of two composites re-parsing YAML. |
| `uploadTestResults` | 5 | Ported in pilot. |
| `publishNotification` | 4 | Slack/email notification → composite with a webhook secret. |
| `uploadIndexFile`, `downloadFromS3`, `uploadToS3`, `getLogsForStage`, `createUploadTestReportManifest` | 3 each | Ported (pilot) or trivial. `getLogsForStage` reads Jenkins stage logs via the Jenkins API — **no GHA equivalent for reading your own in-flight logs**; replace by `tee`-ing command output to a file at the call site (pilot did exactly this in `build-manifest`). |
| `runBwcTestScript`, `runSmokeTestScript`, `runIntegTestScript(ForOSD)`, `validateArtifacts`, `buildManifest`, `assembleManifest`, `buildArchive`, `signArtifacts`, `buildYumRepo`, `copyContainer`, `patchDockerImage`, `scanDockerImage`, `promoteArtifacts`, `promoteRepos`, `promoteContainer`, `publishToMaven`, `uploadMinSnapshotsToS3`, `createSha512Checksums`, `createSignatureFiles`, `createReleaseTag`, `registerReleaseSchedule`, `buildDockerImage` | 1–2 | Straightforward composites. `signArtifacts` (255 lines, 6 credential bindings) is security-sensitive: OIDC + environment-scoped secrets, reviewed by the release team. |

### Group B — becomes a reusable workflow (the step is really a job boundary)

| Step | Fan-in | Why |
| --- | ---: | --- |
| `buildAssembleUpload` | 3 | One full build stage on one agent; pilot's `distribution-build-archive.yml`. |
| `archiveAssembleUpload` + `buildArchive` pair | 2 | Two halves on *different* agents with different images (`distribution-build.jenkinsfile:337–376`); pilot's `distribution-build-package.yml` (two jobs + artifact hand-off). |
| `buildRC` | 1 | Triggers distribution builds for an RC — a cross-workflow dispatch, not a step. |

### Group C — becomes a plain script (Python/bash in this repo, no action wrapper)

The metrics/issue-automation cluster: `publishDistributionBuildResults` (230 ln),
`publishIntegTestResults` (506 ln), `publishSmokeTestResults`, `publishGradleCheckTestResults`,
`indexReleaseState` (318 ln), `createReleaseStateIndices`, `updateBuildFailureIssues`,
`updateIntegTestFailureIssues`, `createGithubIssue`, `closeGithubIssue`, `updateGitHubIssueLabels`,
`updateReleaseIssue`, `gradleCheckFlakyTestDetector`, `gradleCheckFlakyTestGitHubIssue`,
`getCompareBenchmarkIds`, `buildMessage`, `createTestResultsMessage`, and the release-chores
`check*` family (9 steps, 78–171 ln each). These are Groovy programs that build OpenSearch metrics
documents and drive `gh`. The pilot began this correctly (`.github/scripts/metrics_records.py`,
`build_failure_issues.py`); continue in Python where the logic is testable with pytest, and keep
the composite action as a thin invoker only.

### Group D — no GHA equivalent / must be redesigned

| Step | Why it cannot be ported |
| --- | --- |
| `abortStaleJenkinsJobs` | Imports `jenkins.model.Jenkins` and `hudson.model.Result` directly (`vars/abortStaleJenkinsJobs.groovy:19–20`) to kill stale runs. GHA replacement: `concurrency` groups with `cancel-in-progress: true`, or `gh run cancel` via API — different semantics (see §4-F2). |
| `standardReleasePipeline`, `standardReleasePipelineWithGenericTrigger` | These generate a **whole `pipeline {}`** at runtime around a caller-supplied closure (`vars/standardReleasePipelineWithGenericTrigger.groovy:24+`), including a webhook trigger. GHA has no pipeline-generating function; the equivalent is a *reusable workflow* + `repository_dispatch`, and every consuming repo must be changed. Consumers are other OpenSearch repos, not this one (verified: no active pipeline here calls them). |
| `getLogsForStage` | Reads Jenkins build logs over the Jenkins REST API mid-run. Logs of a running GHA job are not API-readable from inside the job; redesign as `tee` at the producing step. |
| `loadCustomScript` | Writes a library resource to the workspace and `chmod +x`s it — replaceable, but only by vendoring the scripts into this repo (they live in the library's `resources/`). |
| `systemdCommands`, `rpmCommands` and the `rpm*Validation` family | Not unportable as *code* (they just run `systemctl`/`rpm`), but they only work when the container has systemd as PID 1 — see §4-F7, the single hardest infrastructure problem in this estate. |

**Highest-leverage conversion order** (fan-in × unblocked pipelines):
1. `runBenchmarkTestScript` + `publishNotification` + `getCompareBenchmarkIds` → unblocks all 6 benchmark pipelines (170 KB of Jenkinsfile).
2. `runBwcTestScript` + `runSmokeTestScript` (+ already-ported test steps) → unblocks bwc/smoke for both products.
3. `validateArtifacts` → unblocks `validate-artifacts` and removes the `build job: 'distribution-validation'` dependency from 4 pipelines.
4. The Group C metrics/issue scripts → unblocks release-chores and the notification jobs.
5. The promote/publish family (`promoteArtifacts`, `promoteRepos`, `promoteContainer`, `publishToMaven`, `signArtifacts` full branches) → unblocks the release wave.

---

## 3. Wave-based migration plan

Each wave is independently shippable; wave N+1 depends only on waves ≤ N. All new workflows stay
`workflow_dispatch`/`workflow_call`-only until cut-over (as the pilot did), so nothing burns
minutes or double-runs while Jenkins remains authoritative.

**Wave 0 — Foundations (prerequisite for everything).**
Deliverables: OIDC providers + per-account IAM roles in the four AWS accounts the pipelines touch
(artifact/public, artifact/production, metrics, benchmark — from the `op://` refs in the
Jenkinsfiles); the GitHub secrets inventory (pilot doc §4 is the starting list); self-hosted runner
groups stood up per §6; a `manifests`-driven parameter-table convention to replace
`parameterizedCron` payloads (§4-F1); decision on the shared-library consumption model (D7).
Must be true before it starts: customer has approved the runner budget and the secrets custody
model (D1, D2).

**Wave 1 — Build spine (harden the pilot).**
`opensearch/distribution-build`, `opensearch/integ-test` (adopt PR #1 after addressing §4a),
then clone for `opensearch-dashboards/distribution-build` + `integ-test` (the OSD files are
structural mirrors; the delta is `runIntegTestScriptForOSD` and the OSD manifests). Also
`check-for-build` (small, but it is the *scheduler* of the spine) and `publish-min-snapshots`
(direct downstream of the build at `distribution-build.jenkinsfile:230`).
Delivers: nightly builds runnable end-to-end on GHA in shadow mode.
Preconditions: Wave 0 runners + OIDC; PR #1 review findings closed.

**Wave 2 — Test estate.**
`bwc-test` ×2, `smoke-test`, `validate-artifacts`, `integ-test-notification`. Preconditions:
Wave 1 build artifacts available; the systemd-runner decision (D3) made — `validate-artifacts`
and the rpm/deb legs of integ/smoke cannot ship without it (§4-F7).

**Wave 3 — Benchmarks.**
The 6 `benchmark-*` pipelines. Preconditions: Wave 0 (benchmark AWS account OIDC), the cron-matrix
externalization pattern (§4-F1) proven in Wave 1 on `feature-build`, and D4 (webhook →
`repository_dispatch` for `benchmark-pull-request`). Long-run limits: these jobs declare
`timeout(time: 24, unit: 'HOURS')` (`benchmark-test.jenkinsfile:26`) — over the 6 h
hosted-job cap; self-hosted runners (5-day cap) required, or the orchestration must poll a
detached CDK execution (§4-F8).

**Wave 4 — Docker/packer/gradle periphery.**
`docker-build`, `docker-copy`, `docker-re-release`, `docker-scan`, `packer-build` (if kept, D6),
`gradle-check-flaky-test-issue-creation`, `feature-build`. Preconditions: Windows docker-builder
runner (for `docker-build`'s Windows leg, `docker-build-lf.jenkinsfile:20,76`) or a decision to
build Windows images another way.

**Wave 5 — Release machinery (last, highest blast radius).**
`release-schedule`, `release-branch`, `release-tag`, `release-notes-*`, `release-chores`,
`release-manifest-commit-lock`, the promote/publish family, and finally `release-promotion` as a
`workflow_dispatch` orchestrator over the reusable workflows created earlier in this wave.
Preconditions: all of Waves 1–2 running in production (the release flow consumes their outputs);
environments with required reviewers configured to replace the "human clicks the button on
Jenkins" gate (§4-F3); signing-key custody decision (D2).

**Wave 6 — Decommission.**
Flip cron/webhook triggers on in GHA, freeze Jenkins jobs one cluster at a time (build → test →
benchmarks → release), run one full release on GHA, then archive `jenkins/`, `tests/jenkins/`,
`build.gradle`'s shared-library test rig, and the `groovy-tests.yml` workflow.

Estimated effort **(inferred)**: Waves 0–1 one working session each; Waves 2–4 one session
combined; Wave 5 one to two sessions plus a real release cycle of shadow-running; external waits
(AWS account changes, runner provisioning, customer approvals) dominate the calendar time.

---

## 4. Flaw catalogue — where Jenkins semantics have no faithful GHA equivalent

### 4a. Critique of the pilot (PR #1) first

The pilot is a competent port, and its `docs/jenkins-to-actions.md` is honest about most gaps. Its
review threads (108 comments) are the best available preview of what full migration will hit.
Concrete criticisms:

1. **Composite-action-per-`vars/` step is the wrong abstraction for the big steps.** 24 composites
   were created 1:1. For thin steps (`download-build-manifest`, `upload-to-s3`) that is fine. But
   `update-build-failure-issues`, `publish-*-results` and `run-integ-test-script` are programs, and
   putting program logic in YAML-embedded bash produced exactly the bug class the review caught:
   the `gh issue list --jq '.[0].number'` → literal `null` bug (review comment 3817361429), the
   two-value `--paths` quoting bug that broke every OSD test run (3817361584), credentials on the
   `curl` command line (3817361932). All of these would have been unit-testable had the logic lived
   in `.github/scripts/*.py` from the start. **Rule for the remaining waves: composite actions may
   contain wiring only; anything with branching lives in a tested script.**
2. **The 10-input `OPTIONS` string is a real cost, not a neutral workaround.** Packing 9 Jenkins
   parameters into a space-separated string (pilot doc §3) recreates stringly-typed CLI parsing
   inside CI, and the review immediately found a defaults-drift issue (comment 3817378481:
   partially-specified `OPTIONS` silently reverts unlisted flags to Jenkins defaults). Alternative
   with better tradeoffs for the *scheduled* pipelines: keep `workflow_dispatch` inputs ≤10 for
   humans, and feed machine dispatches a single `parameters-json` input validated against a schema.
3. **Unresolved review findings are open decisions, not noise.** Still-open threads at the time of
   writing: RC promotion runs for every distribution/arch and ignores `RC_NUMBER` (3817584262);
   Windows zip builds run containerless so the CI-image toolchain is absent (3817586861);
   `container: null`-expression behaviour on Windows jobs is unverified (3817495330 area);
   `CONTINUE_ON_ERROR` no longer surfaces failed plugins anywhere (3817500958). Each is inherited
   by every later wave that copies the pilot's patterns.
4. **The pilot ports smells faithfully (good) but two "fidelity" choices should be reversed at
   cut-over:** the `sleep index*20` S3-throttle stagger (`integ-test.jenkinsfile:222`) should
   become real client-side rate limiting, and the mixed image/args pairing of the rpm build+assemble
   stages (`distribution-build.jenkinsfile:337–376`) should be normalized once the regression corpus
   no longer has to match Jenkins output byte-for-byte.
5. **What the pilot proved that transfers:** matrix-from-manifest generation
   (`manifest_paths.py`), OIDC-per-composite, `stash`→artifacts for the rpm two-agent hand-off, and
   shipping with zero `push`/`pull_request` triggers. These patterns are adopted wholesale in §3.

### 4b. The catalogue

**F1 — `parameterizedCron` has no GHA equivalent. Appears:** `benchmark-test.jenkinsfile` (96
entries), `benchmark-test-vectorsearch` (56), `benchmark-test-endpoint` (21),
`benchmark-test-datafusion` (12), `check-for-build.jenkinsfile:27–33` (3), `publish-min-snapshots`
(4), `docker-re-release` (4), `feature-build` (1), `benchmark-pull-request`,
`gradle-check-flaky-test-issue-creation`. GHA `schedule` triggers take **no parameters** — the
~200 cron entries across the estate are each a cron line *plus a full parameter payload*.
Workarounds: (a) one workflow per parameter set — absurd at 96; (b) a single `schedule` trigger +
a checked-in YAML/JSON table mapping cron slots to parameter sets, with a dispatcher job that
matrixes over "entries due now"; (c) an external scheduler (EventBridge → `repository_dispatch`).
**Recommendation: (b)** — keeps the schedule in-repo and reviewable like `parameterizedCron` is
today; accept the coarser granularity (GHA schedule minimum ~5 min, and scheduled runs are
delayed or dropped under load, which matters for benchmark comparability). Note GHA also
auto-disables schedules after 60 days without repo activity — a non-issue for this active repo but
worth documenting.

**F2 — `lock()` + `abortStaleJenkinsJobs` vs `concurrency`. Appears:**
`check-for-build.jenkinsfile:104` — `lock(resource: "CheckForBuild-${INPUT_MANIFEST}-${TARGET_JOB_NAME}", skipIfLocked: true)`;
`vars/abortStaleJenkinsJobs.groovy` (Jenkins-API run-killing). Jenkins locks are named,
cross-job, FIFO-queued resources; `skipIfLocked: true` means "if a build for this manifest is
already being triggered, do nothing". GHA `concurrency` groups are per-workflow(-ish) keys where
the pending slot holds **exactly one** run — additional runs are cancelled, not queued, and there
is no skip-if-locked primitive (a new run *replaces* the pending one rather than being dropped).
Workarounds: `concurrency: { group: check-for-build-${{ inputs.manifest }}, cancel-in-progress: false }`
approximates skip-if-locked well enough for an hourly poller (the replaced pending run is
equivalent to skipping); for true cross-workflow mutexes a lock artifact/branch or
`softprops/turnstyle`-style polling is needed. **Recommendation:** `concurrency` for
`check-for-build`; audit Wave 5 for any place needing a genuine FIFO (none found in active
pipelines — the release promotion serializes by `wait: true` chaining instead).

**F3 — No `input()` today, but the *implicit* manual gates still need a home.** Verified: zero
`input(` in active pipelines. The release estate is gated by humans manually starting
parameterized jobs in order (`release-promotion` is started by a release manager;
`promote-artifacts` etc. are its children). In GHA, `workflow_dispatch` preserves "a human starts
it", and **environments with required reviewers** add "a second human approves the dangerous job".
**Recommendation:** put `promote-*`, `publish-to-maven`, and `signArtifacts`-touching jobs behind a
`production-release` environment with required reviewers; this is *stronger* than today's Jenkins
model. Tradeoff: environment approvals cannot take parameters and expire after 30 days of waiting.

**F4 — `build job:` chaining and parameter passing. Appears:** `check-for-build.jenkinsfile:116`;
`distribution-build.jenkinsfile:230` (`publish-opensearch-min-snapshots`, `wait: false`), `:1074–1145`
(integ/smoke/bwc/validation/docker triggers); `integ-test.jenkinsfile:108–135` and `:330–345`;
`release-promotion.jenkinsfile` (24 calls, `wait: true`, results consumed);
`release-chores.jenkinsfile` (3); `release-tag.jenkinsfile`; `benchmark-pull-request` →
`benchmark-compare`. Jenkins gives synchronous downstream builds with typed parameters, a return
object, and cross-job queueing. GHA options: reusable workflows (`workflow_call`) give exactly
`wait: true` + outputs, but the whole graph must live in one triggering run and counts against one
run's limits; `gh workflow run` (dispatch) is fire-and-forget — getting `wait: true` back means
polling the API for the run you just created, and *identifying* that run is racy (dispatch returns
no run ID). **Recommendation:** convert `wait: true` chains to `workflow_call` graphs
(release-promotion becomes an orchestrator workflow); convert `wait: false` fire-and-forget calls
to `gh workflow run`. The `while(true) sleep` polling for sibling-stage artifact URLs
(`distribution-build.jenkinsfile:984–1000`) disappears naturally: GHA `needs:` expresses it.

**F5 — Agent labels and stateful, long-lived agents. Appears everywhere:** labels
`Jenkins-Agent-AL2023-X64-M54xlarge-Docker-Host` (30 uses), `...Arm64-M6g4xlarge...`,
`...Ubuntu2404-X64-M52xlarge-Docker-Builder` (6), `...Windows2019-X64-M54xlarge-Docker-Builder`
(`docker-build-lf.jenkinsfile:14–20`), `...Windows2019-X64-M54xlarge-Docker-Host`,
`...AL2023-X64-C54xlarge-Single-Host` / `Arm64-C6g4xlarge-Single-Host`
(`validate-artifacts-lf.jenkinsfile:34–43`), benchmark labels
(`...M52xlarge-Benchmark-Test`). Several pipelines take the label as a *parameter*
(`bwc-test.jenkinsfile:41`). Jenkins agents are warm: pre-pulled images, persistent gradle/maven
caches, docker layer caches. Every GHA job starts cold; on the pilot's hosted runners each build
job re-downloads the CI image and all dependencies. **Recommendation:** self-hosted runner groups
with labels mirroring the agent classes (§6), AMIs built with the images pre-pulled (ironically,
by the successor of `packer-build`), plus `actions/cache` for gradle. Do not accept the pilot's
`ubuntu-24.04` placeholders for anything beyond syntax validation.

**F6 — Credentials: 1Password bindings vs GitHub secrets/OIDC. Appears:** every pipeline defines
`op://` secret refs at the top (e.g. `distribution-build.jenkinsfile:18–22`,
`release-promotion.jenkinsfile:19–23`); the library does `withCredentials`/`withAWS` in ~40 steps.
Jenkins+1Password gives centrally-rotated, human-invisible bindings; GitHub secrets are per-repo
copies with no rotation story, readable by any workflow in the repo (environment secrets narrow
this). The pilot's OIDC-for-AWS move is right and eliminates static AWS keys entirely. For
non-AWS secrets (dockerhub, GitHub bot token, Slack webhook), either copy into GitHub
environment secrets or keep 1Password as source of truth via `1password/load-secrets-action` (a
service-account token still ends up as one GitHub secret). **Recommendation:** OIDC for all four
AWS accounts; environment-scoped secrets for release-only material; `GITHUB_TOKEN` cannot write
issues in *other* repos, so the autocut flows keep needing a bot PAT/App token (pilot found this;
it is a standing custody decision, D2).

**F7 — systemd-as-PID-1 containers for rpm/deb/yum testing. Appears:**
`validate-artifacts-lf.jenkinsfile:28–32` — rpm/yum/deb images run with
`--entrypoint=/usr/lib/systemd/systemd -u root --privileged -v /sys/fs/cgroup:/sys/fs/cgroup:rw --cgroupns=host`;
`vars/systemdCommands.groovy` (`systemctl start/stop/status`); `vars/rpmOpenSearchDistValidation.groovy`
and the rest of the `rpm*Validation` family; the rpm/deb legs of integ/smoke tests. GHA job
containers do **not** honour `--entrypoint` (the runner overrides it with `tail -f /dev/null`) and
the docs don't support systemd PID 1 in `container:`; the pilot's `sanitize_container_options()`
already had to strip `--entrypoint`/`--network`, which silently breaks these images. Options: (a)
skip `container:` and run `docker run --privileged --entrypoint=/usr/lib/systemd/systemd ...` +
`docker exec` from plain steps — works on any docker-capable runner, keeps the images unchanged;
(b) self-hosted runner VMs where systemd is the host's PID 1 and packages install natively — most
faithful, couples tests to runner AMIs; (c) rewrite the tests to not require systemd — largest
change, upstream-visible. **Recommendation: (a)** as the standard pattern (it is scriptable,
image-preserving, and testable), with (b) reserved for `validate-artifacts` if (a) hits cgroup v2
issues on the chosen runner OS.

**F8 — Timeouts: Jenkins 24 h jobs vs GHA limits. Appears:** benchmark pipelines declare
`timeout(time: 24, unit: 'HOURS')`; `integ-test.jenkinsfile:30` declares 12 h; `check-for-build` 5 h;
docker-build 6 h. GHA: 6 h per job on hosted runners, 5 days on self-hosted, 35 days per workflow
run, 72 h `workflow_call` nesting budget. So: every benchmark job **cannot run on hosted runners at
all** for duration reasons alone, and even the 12 h integ-test exceeds the hosted cap when a single
component fans in slowly. **Recommendation:** self-hosted for anything >5 h; additionally, the
benchmark jobs mostly *wait* on a remote CDK-provisioned cluster (`runBenchmarkTestScript` submits
to the benchmark account via `withAWS`, `benchmark-test.jenkinsfile:525` area) — a later
optimization is detach-and-poll (submit, exit, let a scheduled reconciler collect results), which
also frees runner occupancy; do not attempt it in Wave 3's first pass.

**F9 — `GenericTrigger` webhooks. Appears:** `benchmark-pull-request.jenkinsfile:178` onward (JSONPath
extraction of ~25 payload fields, token auth via `tokenCredentialId`, regex filters);
`standardReleasePipelineWithGenericTrigger` in the library (other repos). GHA equivalent is
`repository_dispatch` (payload in `client_payload`, sender needs a token with repo write) or a
small relay (API Gateway/Lambda) translating the existing webhook. The regex-filter semantics
(`regexpFilterExpression: '^true published$'`) become an `if:` on the first job.
**Recommendation:** `repository_dispatch` + payload schema validation in the first job; the
callers (opensearch-project automation that posts the webhook today) must be updated — external
coordination, flagged in D4.

**F10 — Groovy-level dynamism. Appears:** `integ-test.jenkinsfile:209–279` — builds a
`componentTests` map in a `script {}` block and calls `parallel componentTests` (the comment at
`:209` says it outright: "Using scripted pipelines to trigger dynamic parallel stages");
`validate-artifacts-lf.jenkinsfile:216–247` — loops over distributions/architectures allocating
`node(...)` and `docker.image(...).inside(...)` per iteration; `distribution-build`'s
cross-stage `env.ARTIFACT_URL_*` mutation consumed by a polling loop (`:984`); the library's
pipeline-generating `standardReleasePipeline*`. GHA workflows are static documents; the only
runtime dynamism is `strategy.matrix` fed by `fromJSON()` of a previous job's output. That covers
the fan-outs (pilot proved it) with two hard limits: **256 jobs per matrix** (fine: the largest
fan-out is the integ-test component list, ~30–50 components per manifest) and **the empty matrix is
an error**, needing a `has-components` guard (pilot hit this, doc §7). What matrices cannot
express: per-branch nested `docker.inside` with different images *within one job* — those become
sequential steps or separate matrix dimensions. `gradle-check-flaky-test-issue-creation` and
anything else calling library steps that reflect on Jenkins state (`abortStaleJenkinsJobs`,
`getLogsForStage`) needs redesign per §2 Group D.

**F11 — `buildDiscarder`/retention. Appears:** `logRotator(daysToKeepStr:)` in 14 active pipelines
(7–180 days; `gradle-check-flaky-test-issue-creation.jenkinsfile:32` wants 180). GHA log/artifact
retention is a repo/org setting (max 90 days for private repos, 400 for public artifacts? — the
90-day log ceiling is the binding constraint **(inferred from GitHub docs; verify current limits)**),
plus per-artifact `retention-days`. The 180-day requirement cannot be met in-platform.
**Recommendation:** rely on the fact that the durable record already lives outside CI (S3 +
the metrics cluster); set repo retention to 90 days and export anything needed longer to S3.

**F12 — `stash`/`unstash`. Appears:** `integ-test.jenkinsfile:195/:228` (OSD variant `:` same),
`distribution-build.jenkinsfile:357–391` etc. (via `buildArchive`/`archiveAssembleUpload`
`stashName` params), `release-notes-generate.jenkinsfile`, `benchmark-test-endpoint`,
`validate-artifacts`. Maps to `actions/upload-artifact`/`download-artifact` (pilot did this).
Tradeoffs: artifacts are slower, count against storage quota, and are visible/downloadable to
anyone with repo read; Jenkins stashes were build-private and free. The integ-test "stash the
whole workspace" (`stash includes: '**'`) is expensive as an artifact — prefer re-checkout +
download-from-S3 in the component job, which the code path already supports
(`integ-test.jenkinsfile:230`).

**F13 — `UNSTABLE` build status.** `distribution-build.jenkinsfile:1044–1052, 1067–1073`
downgrades to UNSTABLE when only some plugins fail. GHA has success/failure/cancelled only.
Pilot's answer (a `::warning` annotation) loses the machine-readable tri-state that
`updateBuildFailureIssues` and the metrics documents key off. **Recommendation:** carry the
tri-state in the published metrics document and a job output; treat the GHA conclusion as binary.

---

## 5. Testing strategy

What exists today: `tests/jenkins/` runs
[jenkins-pipeline-unit](https://github.com/jenkinsci/JenkinsPipelineUnit) 1.13 via Gradle
(`build.gradle:41`, sourceSets at `:61/:67`), executing each Jenkinsfile against mocked steps and
snapshot-comparing the rendered call tree to `tests/jenkins/jenkinsjob-regression-files/**` (~34
test classes; run in CI by `.github/workflows/groovy-tests.yml` on every push/PR). The shared
library has its own Groovy/Spock suite upstream.

That suite tests *pipeline structure*, not build correctness. Its GHA replacement is a different
stack per layer:

1. **Static validation:** `actionlint` + `yamllint` on all workflows/actions in CI (the pilot ran
   these manually; make them a workflow). This replaces "does the Jenkinsfile compile".
2. **Logic tests:** everything moved into `.github/scripts/*.py` (§2 Group C, and
   `manifest_paths.py`) gets pytest coverage in the existing `python-tests.yml`. This is where the
   pilot's review bugs (jq-null, quoting, defaults drift) would have been caught. Parity fixtures:
   run the Python accessors against the same manifests the Groovy tests use and assert identical
   output — the pilot already did this once by hand against `manifests/3.9.0` (pilot doc §7);
   freeze it as a test.
3. **Rendered-pipeline regression equivalent:** there is no GHA analogue of "render the pipeline
   and snapshot it" (workflows don't render without running). Closest substitutes, in order of
   value: (a) snapshot-test the *generated matrices and parameter tables* (pure functions →
   JSON), which is where this estate's real variability lives; (b) `act` for smoke-running small
   workflows locally — limited fidelity **(inferred: `act` cannot emulate `container:` options,
   OIDC, or self-hosted labels)**; (c) a scheduled `workflow_dispatch` dry-run mode where every
   composite that would mutate the world (S3 upload, `gh issue`, docker push) is switched to
   echo — add a `dry-run` input plumbed through the reusable workflows during Waves 1–5.
4. **Live validation without prod infra:** shadow runs against a sandbox AWS account (separate
   OIDC role, separate bucket, separate metrics index) dispatched from the fork; compare the S3
   layout and metrics documents against a same-manifest Jenkins run byte-for-byte. This is the
   only trustworthy end-to-end signal and is the gate for each wave's cut-over.
5. **During transition:** keep `tests/jenkins/` green for every wave until Wave 6 — the
   Jenkinsfiles remain the production system until decommission, so the Groovy suite keeps its
   job exactly as long as the files it tests do.

---

## 6. Cost, scale and infrastructure notes

Where GitHub-hosted runners do **not** fit (each verified against the files cited):

| Need | Evidence | Hosted-runner status |
| --- | --- | --- |
| ARM64 Linux builders (m6g/c6g class) | arm64 stages in both distribution-builds; `validate-artifacts-lf.jenkinsfile:36` | `ubuntu-24.04-arm` exists but is 4-vCPU class; distribution builds on `M6g4xlarge` (16 vCPU) — underpowered **(inferred sizing)** |
| Large x64 builders | `M54xlarge`/`C54xlarge` labels (16 vCPU/64 GB) across 30 uses | Standard hosted = 4 vCPU/16 GB; larger hosted runners exist at premium per-minute rates |
| Windows with docker named-pipe | `docker-build-lf.jenkinsfile:20` (`//./pipe/docker_engine`) | Hosted `windows-2022` has no usable Windows-container docker daemon for this workflow shape — self-hosted required |
| systemd PID-1 containers | §4-F7 | Not supported in hosted `container:`; needs docker-run pattern or self-hosted VMs |
| >6 h jobs | benchmark 24 h, integ-test 12 h (§4-F8) | Hard 6 h hosted cap — self-hosted only |
| docker.sock / `--privileged` | `docker-copy-lf.jenkinsfile:58`, validate-artifacts args | Hosted runners allow docker but job `container:` cannot mount the socket the way these args do; run docker from steps instead |
| macOS/darwin | **None found.** No active pipeline uses a mac label; `signArtifacts` has a mac branch (`vars/signArtifacts.groovy`) unreachable from this repo's pipelines | No macOS runners needed for this estate |

Runner-fleet proposal (mirrors today's agent classes): `linux-x64-xl` (m5.4xlarge-class, docker),
`linux-arm64-xl` (m6g.4xlarge-class), `linux-x64-benchmark`, `windows-2019-docker`,
`linux-single-host` (for validate-artifacts' native/systemd runs). Reuse the AMI pipeline:
`packer-build-lf.jenkinsfile` already builds agent AMIs; its successor builds runner AMIs.

Actions-minutes exposure: with self-hosted runners, minutes cost ≈ 0 (self-hosted is free on GHA;
you pay EC2). The realistic cost line is EC2 for the fleet plus artifact storage. Artifact storage
is the sleeper: the integ-test workspace stash (§4-F12) and per-stage build logs multiply quickly;
keep large intermediates on S3 as Jenkins does today and set short `retention-days` on artifacts.
Nightly load today **(inferred from cron tables)**: ~3 distribution builds + downstream test fans
+ ~185 benchmark cron entries/day across the benchmark pipelines — the benchmark fleet is the
dominant compute item, unchanged from today because the heavy lifting is CDK clusters in AWS, not
the runner.

---

## 7. Open decisions for the customer

* **D1 — Runner budget and ownership.** Approve the self-hosted fleet (§6) and who operates it
  (the packer/AMI pipeline implies in-house ops today). Without this, only S-complexity pipelines
  can ship.
* **D2 — Secrets custody.** Copy secrets into GitHub environments, or keep 1Password as source of
  truth via a load action? Separately: which bot identity (PAT vs GitHub App) gets cross-repo
  issue-write for the autocut flows, and who owns RPM signing key access (§4-F6).
* **D3 — systemd testing pattern.** Pick (a) docker-run-from-steps, (b) systemd runner VMs, or
  (c) test rewrite (§4-F7). Blocks Wave 2's rpm/deb legs and `validate-artifacts`.
* **D4 — Webhook producers.** Who updates the systems that POST to the Jenkins `GenericTrigger`
  endpoint (benchmark-pull-request) to send `repository_dispatch` instead (§4-F9)? External to
  this repo.
* **D5 — Benchmark schedule fidelity.** GHA `schedule` is best-effort (delays/drops under load).
  Are nightly benchmark start-time jitters acceptable, or is an EventBridge-driven dispatcher
  required (§4-F1)?
* **D6 — Keep or retire `packer-build`?** If the runner fleet is built with the same Packer
  templates, this pipeline survives renamed; if runners are managed differently (e.g.
  actions-runner-controller on EKS), retire it.
* **D7 — Shared-library blast radius.** `opensearch-build-libraries` is consumed by other
  OpenSearch repos (`standardReleasePipeline*`, `publishTo*` families are unused *here* but used
  *there*). Is this migration allowed to leave the library behind for those consumers, or must the
  replacement actions/scripts be published for ecosystem-wide reuse (separate actions repo)?
* **D8 — Delete `jenkins/legacy/`?** It is untested dead weight; confirm nothing references it.
* **D9 — Retention.** Accept 90-day in-platform retention with S3 export for the 180-day
  requirement (§4-F11)?
* **D10 — Pilot adoption.** Merge PR #1 as Wave 1's base after closing its open review findings
  (§4a-3), or restart the two pilot pipelines under the script-first rule (§4a-1)? Recommendation:
  merge-then-refactor; the review record is valuable history.
