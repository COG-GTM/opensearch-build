#!/bin/bash -e

# Copyright OpenSearch Contributors
# SPDX-License-Identifier: Apache-2.0
#
# The OpenSearch Contributors require contributions made to
# this file be licensed under the Apache-2.0 license or a
# compatible open source license.

# Ubuntu variant of the multi-runner module's templates/user-data.sh (which assumes AL2023: dnf, ec2-user).
# Rendered by the module with the same template variables.

export DEBIAN_FRONTEND=noninteractive

install_with_retry() {
  max_attempts=5
  attempt_count=0
  success=false
  while [ $success = false ] && [ $attempt_count -le $max_attempts ]; do
    echo "Attempting $attempt_count/$max_attempts: Installing $*"
    if apt-get update -q && apt-get install -y -q "$@"; then
      success=true
    else
      echo "Failed to install $1 - retrying"
      attempt_count=$(( attempt_count + 1 ))
      sleep 5
    fi
  done
  [ $success = true ]
}

exec > >(tee /var/log/user-data.log | logger -t user-data -s 2>/dev/console) 2>&1

set +x

%{ if enable_debug_logging }
set -x
%{ endif }

# unattended-upgrades / apt-daily hold the dpkg lock shortly after boot (same as the module's Ubuntu example).
systemctl stop unattended-upgrades apt-daily.service apt-daily-upgrade.service 2>/dev/null || true
systemctl mask unattended-upgrades apt-daily.service apt-daily-upgrade.service 2>/dev/null || true
while fuser /var/lib/dpkg/lock-frontend >/dev/null 2>&1; do
  echo "Waiting for dpkg lock to be released..."
  sleep 5
done

${pre_install}

command -v docker >/dev/null || install_with_retry docker.io
systemctl enable --now docker
usermod -a -G docker ubuntu

install_with_retry jq git curl unzip
# The module's start script needs the AWS CLI (ssm get-parameter, ec2 terminate-instances).
if ! command -v aws >/dev/null; then
  cli_arch=$(uname -m)
  curl -fsSL -o /root/awscliv2.zip "https://awscli.amazonaws.com/awscli-exe-linux-$cli_arch.zip"
  unzip -q -d /root /root/awscliv2.zip
  /root/aws/install
  rm -rf /root/aws /root/awscliv2.zip
fi
aws --version
if ! dpkg -s amazon-cloudwatch-agent >/dev/null 2>&1; then
  arch=$(dpkg --print-architecture)
  curl -fsSL -o /tmp/amazon-cloudwatch-agent.deb "https://amazoncloudwatch-agent.s3.amazonaws.com/ubuntu/$arch/latest/amazon-cloudwatch-agent.deb"
  dpkg -i -E /tmp/amazon-cloudwatch-agent.deb
  rm -f /tmp/amazon-cloudwatch-agent.deb
fi

user_name=ubuntu

${install_runner}

${post_install}

%{ if hook_job_started != "" }
cat > /opt/actions-runner/hook_job_started.sh <<'HOOK'
${hook_job_started}
HOOK
echo ACTIONS_RUNNER_HOOK_JOB_STARTED=/opt/actions-runner/hook_job_started.sh | tee -a /opt/actions-runner/.env
%{ endif }

%{ if hook_job_completed != "" }
cat > /opt/actions-runner/hook_job_completed.sh <<'HOOK'
${hook_job_completed}
HOOK
echo ACTIONS_RUNNER_HOOK_JOB_COMPLETED=/opt/actions-runner/hook_job_completed.sh | tee -a /opt/actions-runner/.env
%{ endif }

${start_runner}
