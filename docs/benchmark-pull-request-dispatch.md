# Requesting a pull request benchmark (`repository_dispatch`)

`jenkins/opensearch/benchmark-pull-request.jenkinsfile` was started by a `GenericTrigger` webhook that a workflow in
the OpenSearch repository called when a maintainer commented on a pull request. In GitHub Actions the same request
is a [`repository_dispatch`](https://docs.github.com/en/rest/repos/repos#create-a-repository-dispatch-event) event
of type `benchmark-pull-request` sent to `COG-GTM/opensearch-build`, which runs
[`.github/workflows/benchmark-pull-request.yml`](../.github/workflows/benchmark-pull-request.yml). GitHub
authenticates the call, so the `jenkins-pr-benchmark-generic-webhook-token` has no replacement (ADR 0001).

## Payload

`client_payload` carries the fields the Jenkins trigger extracted (`genericVariables`), with the same names:

| Field | Required | Meaning |
| --- | --- | --- |
| `pull_request_number` | for the comment | Pull request to comment on (digits only) |
| `repository` | for the comment | `owner/repo` of that pull request |
| `baseline_cluster_config` | no | `user-tags.cluster-config` of the nightly baseline; when set, `benchmark-compare.yml` is dispatched with the latest baseline (last 5 days, `<DISTRIBUTION_VERSION>-SNAPSHOT`) and the contender tagged `pull_request_number:<n>` |
| `DISTRIBUTION_URL` | yes | Public x64 tarball built from the pull request |
| `DISTRIBUTION_VERSION` | yes | Its version, e.g. `3.10.0` |
| `TEST_WORKLOAD` | no | Defaults to `nyc_taxis` |
| `SECURITY_ENABLED`, `SINGLE_NODE_CLUSTER`, `MIN_DISTRIBUTION`, `CAPTURE_NODE_STAT` | no | `"true"` / `"false"` |
| `MANAGER_NODE_COUNT`, `DATA_NODE_COUNT`, `CLIENT_NODE_COUNT`, `INGEST_NODE_COUNT`, `ML_NODE_COUNT`, `DATA_INSTANCE_TYPE`, `DATA_NODE_STORAGE`, `ML_NODE_STORAGE`, `JVM_SYS_PROPS`, `ADDITIONAL_CONFIG`, `HEAP_SIZE_IN_GB`, `USER_TAGS`, `WORKLOAD_PARAMS`, `TEST_PROCEDURE`, `EXCLUDE_TASKS`, `INCLUDE_TASKS`, `TELEMETRY_PARAMS` | no | Same meaning as the Jenkins parameters |
| `PLUGIN_URL`, `PRESERVE_CLUSTER`, `TEST_MODE` | no | Not extracted by the Jenkins trigger; accepted here too (`TEST_MODE` runs opensearch-benchmark `--test-mode`, for checks only) |
| `OPTIONS` | no | Any of the parameters above as `KEY=VALUE` pairs separated by `;`, for requests that would exceed the 10-property limit below |
| `dry_run` | no | `true` renders the pull request comment and the compare queries in the run log and step summary, and posts or dispatches nothing |

Unknown fields, a non-numeric `pull_request_number`, a `repository` that is not `owner/repo`, and values containing
shell metacharacters (`' $ \` ; | & < > \`, newlines) fail the run in `validate-and-set-parameters`.
`USER_TAGS` should contain `pull_request_number:<n>`: that tag is how `benchmark-compare` finds the contender.

## Sender workflow (in the requesting repository)

The sender needs a token that may create dispatch events on `COG-GTM/opensearch-build` (a GitHub App installation
token with `contents: write` on that repository, or a fine-grained PAT with the same permission). The snippet below
keeps the comment trigger the Jenkins webhook used; adapt the `if:` to the repository's own allow-list of who may
request benchmarks.

```yaml
name: benchmark-pull-request
on:
  issue_comment:
    types: [created]
permissions: {}
jobs:
  dispatch:
    if: github.event.issue.pull_request && startsWith(github.event.comment.body, '"run-benchmark-test"')
    runs-on: ubuntu-latest
    steps:
      - id: token
        uses: actions/create-github-app-token@64ab729a6294000f5aa4a4bc0b73d1f32bc45510  # v3.3.0
        with:
          app-id: ${{ secrets.BENCHMARK_DISPATCH_APP_ID }}
          private-key: ${{ secrets.BENCHMARK_DISPATCH_APP_PRIVATE_KEY }}
          owner: COG-GTM
          repositories: opensearch-build
      - name: Send repository_dispatch
        env:
          GH_TOKEN: ${{ steps.token.outputs.token }}
          PR_NUMBER: ${{ github.event.issue.number }}
          # the same fields the Jenkins webhook body carried; distribution-url / version come from the PR build
          DISTRIBUTION_URL: https://ci.opensearch.org/ci/dbc/pr-builds/${{ github.event.issue.number }}/opensearch-min-3.10.0-SNAPSHOT-linux-x64.tar.gz
          DISTRIBUTION_VERSION: 3.10.0
          BASELINE_CLUSTER_CONFIG: x64-r5.xlarge-single-node-1-shard-0-replica-baseline
        run: |
          jq -n \
            --arg pr "$PR_NUMBER" --arg repo "$GITHUB_REPOSITORY" --arg url "$DISTRIBUTION_URL" \
            --arg version "$DISTRIBUTION_VERSION" --arg baseline "$BASELINE_CLUSTER_CONFIG" \
            '{event_type: "benchmark-pull-request", client_payload: {
               pull_request_number: $pr, repository: $repo, baseline_cluster_config: $baseline,
               DISTRIBUTION_URL: $url, DISTRIBUTION_VERSION: $version, TEST_WORKLOAD: "nyc_taxis",
               OPTIONS: "SINGLE_NODE_CLUSTER=true;MIN_DISTRIBUTION=true",
               USER_TAGS: ("pull_request_number:" + $pr + ",cluster-config:x64-r5.xlarge-single-node-1-shard-0-replica"),
               WORKLOAD_PARAMS: "{\"number_of_replicas\":\"0\",\"number_of_shards\":\"1\"}"}}' |
            gh api repos/COG-GTM/opensearch-build/dispatches --input -
```

GitHub caps `client_payload` at 10 top-level properties
([REST docs](https://docs.github.com/en/rest/repos/repos#create-a-repository-dispatch-event)); the example sends 8
and packs the rest into `OPTIONS`. Send only the parameters that differ from the defaults.

## What comes back

* Success: the comment Jenkins posted, with the Actions run URL in place of the Jenkins `BUILD_URL`:

  ```
  <details><summary>Benchmark Results</summary>

  #### Benchmark Results for Job: https://github.com/COG-GTM/opensearch-build/actions/runs/<run id>
  <opensearch-benchmark final_result.md>
  </details>
  ```

* Failure: `The benchmark job <run url> failed.` followed by a line ` Please see logs to debug.`
* With `baseline_cluster_config`: `benchmark-compare.yml` posts a second comment with the summary
  `Benchmark Baseline Comparison Results` and the `opensearch-benchmark compare` table. When the datastore has no
  baseline or contender the compare is skipped with a notice (Jenkins passed `null` on and the compare job failed).

`.github/workflows/benchmark-dry-run.yml` sends the payload above (with `"dry_run": true`) through
`benchmark-pull-request.yml` on every pull request that touches the benchmark workflows.
