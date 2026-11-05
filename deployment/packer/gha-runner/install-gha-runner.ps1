# Copyright OpenSearch Contributors
# SPDX-License-Identifier: Apache-2.0
#
# The OpenSearch Contributors require contributions made to
# this file be licensed under the Apache-2.0 license or a
# compatible open source license.

# Bakes the GitHub Actions runner agent into a Windows agent AMI (ADR 0001). Runs from the packer
# powershell provisioner added by render_template.py. Nothing is registered here: the ephemeral
# runner gets its JIT config at boot from the terraform-aws-github-runner start script.

$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'

foreach ($name in 'RUNNER_VERSION', 'RUNNER_PLATFORM', 'RUNNER_SHA256') {
    if (-not [Environment]::GetEnvironmentVariable($name)) { throw "$name is not set" }
}

$runnerDir = 'C:\actions-runner'
$zip = "actions-runner-$env:RUNNER_PLATFORM-$env:RUNNER_VERSION.zip"
$zipPath = Join-Path $env:TEMP $zip

New-Item -ItemType Directory -Force -Path $runnerDir, 'C:\hostedtoolcache' | Out-Null
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
Invoke-WebRequest -UseBasicParsing -Uri "https://github.com/actions/runner/releases/download/v$env:RUNNER_VERSION/$zip" -OutFile $zipPath

$actual = (Get-FileHash -Algorithm SHA256 -Path $zipPath).Hash.ToLowerInvariant()
if ($actual -ne $env:RUNNER_SHA256.ToLowerInvariant()) { throw "SHA-256 mismatch for ${zip}: $actual" }

Expand-Archive -Path $zipPath -DestinationPath $runnerDir -Force
Remove-Item $zipPath
& "$runnerDir\bin\Runner.Listener.exe" --version
