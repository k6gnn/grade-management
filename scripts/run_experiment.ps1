# run_experiment.ps1
# Usage: .\scripts\run_experiment.ps1 <experiment_id> <run_number> <injection_type> <platform>
# Example: .\scripts\run_experiment.ps1 E1 1 compilation github

param(
    [Parameter(Mandatory=$true)] [string]$ExperimentId,
    [Parameter(Mandatory=$true)] [int]$RunNumber,
    [Parameter(Mandatory=$true)] [string]$InjectionType,
    [Parameter(Mandatory=$true)] [string]$Platform
)

$ErrorActionPreference = "Stop"

$INJECT_SCRIPT  = "scripts\inject_failure.py"
$COLLECT_SCRIPT = "scripts\collect_results.py"

Write-Host ""
Write-Host "============================================================"
Write-Host "  EXPERIMENT: $ExperimentId  RUN: $RunNumber  PLATFORM: $Platform"
Write-Host "  INJECTION:  $InjectionType"
Write-Host "============================================================"

# Step 1: Ensure clean baseline
Write-Host ""
Write-Host "[1/6] Ensuring clean baseline..."
python $INJECT_SCRIPT restore 2>$null
Write-Host "  Baseline clean."

# Step 2: Inject failure
Write-Host ""
Write-Host "[2/6] Injecting failure: $InjectionType..."
python $INJECT_SCRIPT $InjectionType
if ($LASTEXITCODE -ne 0) {
    Write-Host "ERROR: Injection failed. Aborting."
    exit 1
}

# Step 3: Commit and push
Write-Host ""
Write-Host "[3/6] Committing and pushing to $Platform..."

$CommitMsg = "experiment: $ExperimentId run $RunNumber - $InjectionType - $Platform"
git add -A
git commit -m $CommitMsg

if ($Platform -eq "github") {
    git push origin main
    Write-Host "  Pushed to GitHub (origin/main)"
} elseif ($Platform -eq "gitlab") {
    git push gitlab main
    Write-Host "  Pushed to GitLab (gitlab/main)"
} elseif ($Platform -eq "jenkins") {
    git push origin main
    Write-Host "  Pushed to GitHub for Jenkins webhook trigger"
} else {
    Write-Host "ERROR: Unknown platform '$Platform'. Use: github | gitlab | jenkins"
    exit 1
}

# Capture the inject commit SHA immediately after push
$InjectSHA = (git rev-parse HEAD).Trim()
Write-Host "  Inject commit SHA: $InjectSHA"

# Step 4: Collect results
Write-Host ""
Write-Host "[4/6] Waiting for pipeline and collecting results..."
python $COLLECT_SCRIPT $ExperimentId $RunNumber $Platform $InjectionType --commit $InjectSHA
if ($LASTEXITCODE -ne 0) {
    Write-Host "WARNING: Collection had errors - check output above."
}

# Step 5: Restore and commit
Write-Host ""
Write-Host "[5/6] Restoring to clean baseline..."
python $INJECT_SCRIPT restore
if ($LASTEXITCODE -ne 0) {
    Write-Host "ERROR: Restore failed. Fix manually before next run."
    exit 1
}

$RestoreMsg = "restore: $ExperimentId run $RunNumber - $InjectionType - $Platform"
git add -A
git commit -m $RestoreMsg

if ($Platform -eq "gitlab") {
    git push gitlab main
} else {
    git push origin main
}

Write-Host "  Restore committed and pushed."

# Step 6: Summary
Write-Host ""
Write-Host "[6/6] Run complete."
Write-Host "  Results saved in: experiment_results\$ExperimentId\$Platform\run_$RunNumber\"
Write-Host "  CSV updated:      experiment_results\results.csv"
Write-Host ""
