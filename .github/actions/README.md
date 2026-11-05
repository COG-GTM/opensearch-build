# Composite actions

Building blocks that replace the `opensearch-build-libraries` steps classified as **composite** in
[`docs/jenkins-gha-inventory.md`](../../docs/jenkins-gha-inventory.md) (s1.2), using the runners, OIDC roles and
secret names of [ADR 0001](../../docs/adr/0001-gha-runners-and-secrets.md). Each `action.yml` documents its inputs
and outputs; [`actions-smoke.yml`](../workflows/actions-smoke.yml) exercises them on `ubuntu-latest`.

| Action | Replaces (shared-library step) | AWS role (secret) |
| --- | --- | --- |
| [`resolve-input-manifest`](resolve-input-manifest/action.yml) | `checkout scm` + `InputManifest` read, `detectDockerAgent` | none |
| [`download-build-manifest`](download-build-manifest/action.yml) | `downloadBuildManifest` (+ `BuildManifest` accessors, ADR runner labels) | none |
| [`s3-sync`](s3-sync/action.yml) | `uploadToS3`, `downloadFromS3`, `uploadTestResults`, `uploadIndexFile` | `AWS_ROLE_ARTIFACTS`, `AWS_ROLE_TEST_RESULTS`, `AWS_ROLE_PROMOTION` |
| [`sign-artifacts`](sign-artifacts/action.yml) | `signArtifacts`, `createSignatureFiles` | `AWS_ROLE_SIGNING_BASE` (signer client), `AWS_ROLE_RPM_SIGNING` (rpm) |
| [`checksums`](checksums/action.yml) | `createSha512Checksums` | none |
| [`assemble-upload`](assemble-upload/action.yml) | `assembleUpload` (`assembleManifest` + `buildYumRepo` + `uploadArtifacts`), `uploadIndexFile`; called by [`_build-distribution.yml`](../workflows/_build-distribution.yml) | `AWS_ROLE_ARTIFACTS`, `AWS_ROLE_RPM_SIGNING` (rpm) |
| [`publish-metrics`](publish-metrics/action.yml) | `publishDistributionBuildResults` (`kind: build`), `publishIntegTestResults` (`kind: integ`) | `AWS_ROLE_METRICS` |
| [`container-copy`](container-copy/action.yml) | `copyContainer` | `AWS_ROLE_PROMOTION` (production ECR Public only) |
| [`trivy-scan`](trivy-scan/action.yml) | `scanDockerImage` | none |

`setup-opensearch-dashboards` and `start-opensearch` are consumed by plugin repositories and are unrelated to the
Jenkins library.

## Conventions

* Third-party actions are pinned to a full commit SHA with the version in a comment.
* No static AWS keys: actions that call AWS take `role-to-assume` (the role ARN secret) and assume it with
  `aws-actions/configure-aws-credentials` (`audience: sts.amazonaws.com`, session `gha-<run_id>-<run_attempt>`). The
  calling job declares `permissions: id-token: write`.
* Composite actions cannot read `secrets`. Callers pass role ARNs, bucket names and endpoints as inputs, and export
  the signing secrets as environment variables of the same name (see below).
* Inputs reach shell steps through `env:` only, never by `${{ }}` interpolation inside `run:`.
* Python helpers live in [`../scripts`](../scripts) (tests in `tests/tests_github_scripts`). They only need PyYAML,
  so they run on a bare runner or inside any CI-runner image without `pipenv install`.

## Example

```yaml
jobs:
  promote:
    runs-on: [self-hosted, linux, x64, al2023-x64-m5-4xlarge-docker-host]
    environment: release
    permissions:
      contents: read
      id-token: write
    steps:
      - uses: actions/checkout@de0fac2e4500dabe0009e67214ff5f5447ce83dd # v6.0.2
        with:
          persist-credentials: false
      - id: manifest
        uses: ./.github/actions/download-build-manifest
        with:
          url: https://ci.opensearch.org/ci/dbc/distribution-build-opensearch/3.9.0/11089/linux/x64/tar/dist/opensearch/manifest.yml
      - uses: ./.github/actions/s3-sync
        with:
          direction: download
          role-to-assume: ${{ secrets.AWS_ROLE_PROMOTION }}
          bucket: ${{ secrets.ARTIFACT_BUCKET_NAME }}
          remote-path: ${{ steps.manifest.outputs.artifact-root }}/dist/opensearch/
          local-path: artifacts
      - uses: ./.github/actions/sign-artifacts
        env:
          SIGNER_CLIENT_ROLE: ${{ secrets.SIGNER_CLIENT_ROLE }}
          SIGNER_CLIENT_EXTERNAL_ID: ${{ secrets.SIGNER_CLIENT_EXTERNAL_ID }}
          SIGNER_CLIENT_UNSIGNED_BUCKET: ${{ secrets.SIGNER_CLIENT_UNSIGNED_BUCKET }}
          SIGNER_CLIENT_SIGNED_BUCKET: ${{ secrets.SIGNER_CLIENT_SIGNED_BUCKET }}
        with:
          artifact-path: artifacts
          role-to-assume: ${{ secrets.AWS_ROLE_SIGNING_BASE }}
      - uses: ./.github/actions/checksums
        with:
          artifact-path: artifacts
```

## Signing secrets by path

`sign-artifacts` checks that these environment variables are set before it assumes the role (`dry-run: true` lists
them without signing). All are `release`-environment secrets in ADR 0001.

| `sigtype` / `platform` | Environment variables |
| --- | --- |
| `.rpm` | `RPM_SIGNING_KEY_ID`, `RPM_SIGNING_PASSPHRASE_SECRETS_ARN`, `RPM_SIGNING_SECRET_KEY_ID_SECRETS_ARN`, `RPM_RELEASE_SIGNING_KEY_ID`, `RPM_RELEASE_SIGNING_PASSPHRASE_SECRETS_ARN`, `RPM_RELEASE_SIGNING_SECRET_KEY_ID_SECRETS_ARN` |
| `.sig` / `.asc`, `linux` (PGP) | `SIGNER_CLIENT_ROLE`, `SIGNER_CLIENT_EXTERNAL_ID`, `SIGNER_CLIENT_UNSIGNED_BUCKET`, `SIGNER_CLIENT_SIGNED_BUCKET` |
| `windows` | `SIGNER_WINDOWS_ROLE`, `SIGNER_WINDOWS_EXTERNAL_ID`, `SIGNER_WINDOWS_UNSIGNED_BUCKET`, `SIGNER_WINDOWS_SIGNED_BUCKET`, `SIGNER_WINDOWS_PROFILE_IDENTIFIER`, `SIGNER_WINDOWS_PLATFORM_IDENTIFIER` |
| `mac` | `SIGNER_MAC_ROLE`, `SIGNER_MAC_EXTERNAL_ID`, `SIGNER_MAC_UNSIGNED_BUCKET`, `SIGNER_MAC_SIGNED_BUCKET` |
| `jar_signer` | `JAR_SIGNER_ROLE`, `JAR_SIGNER_EXTERNAL_ID`, `JAR_SIGNER_UNSIGNED_BUCKET`, `JAR_SIGNER_SIGNED_BUCKET` |
