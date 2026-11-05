#!/bin/bash -e

# Copyright OpenSearch Contributors
# SPDX-License-Identifier: Apache-2.0
#
# The OpenSearch Contributors require contributions made to
# this file be licensed under the Apache-2.0 license or a
# compatible open source license.

# multi-runner module templates/user-data-osx.sh (v8.1.0) for macOS pools, without install_runner.
# The agent is baked into the AMI (deployment/packer); post_install carries templates/baked-runner.*.tftpl,
# which checks its version and fails the instance instead of downloading a runner.

set +x

%{ if enable_debug_logging }
set -x
%{ endif }

${pre_install}

# On macOS we don't use dnf; assume base image has required tools
# Optionally use brew here if needed
if command -v brew >/dev/null 2>&1; then
  echo "Homebrew detected; you can install extra dependencies via brew if needed"
fi

user_name=ec2-user

${post_install}

# Register runner job hooks
# Ref: https://docs.github.com/en/actions/hosting-your-own-runners/managing-self-hosted-runners/running-scripts-before-or-after-a-job
%{ if hook_job_started != "" }
cat > /opt/actions-runner/hook_job_started.sh <<'EOF'
${hook_job_started}
EOF
echo ACTIONS_RUNNER_HOOK_JOB_STARTED=/opt/actions-runner/hook_job_started.sh | tee -a /opt/actions-runner/.env
%{ endif }

%{ if hook_job_completed != "" }
cat > /opt/actions-runner/hook_job_completed.sh <<'EOF'
${hook_job_completed}
EOF
echo ACTIONS_RUNNER_HOOK_JOB_COMPLETED=/opt/actions-runner/hook_job_completed.sh | tee -a /opt/actions-runner/.env
%{ endif }

${start_runner}
