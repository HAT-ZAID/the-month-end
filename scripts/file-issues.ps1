<#
.SYNOPSIS
    File the issue corpus in docs/issues/ into GitHub Issues.

.DESCRIPTION
    Requires the GitHub CLI (https://cli.github.com) and `gh auth login`.
    Each file in docs/issues/*.md carries YAML front matter with `title:` and
    `labels:`; the rest of the file is the body.

.PARAMETER Repo
    Target repository as owner/name. Defaults to the repo gh resolves from the
    current directory.

.PARAMETER DryRun
    Print what would be created without calling GitHub.

.PARAMETER EnsureLabels
    Create any missing labels first (colour defaults to grey).

.EXAMPLE
    ./scripts/file-issues.ps1 -DryRun
    ./scripts/file-issues.ps1 -Repo HAT-ZAID/hackerrank-orchestrate-september26 -EnsureLabels
#>
[CmdletBinding()]
param(
    [string]$Repo = "",
    [switch]$DryRun,
    [switch]$EnsureLabels
)

$ErrorActionPreference = "Stop"

if (-not (Get-Command gh -ErrorAction SilentlyContinue)) {
    throw "GitHub CLI not found. Install it from https://cli.github.com and run: gh auth login"
}

$issueDir = Join-Path $PSScriptRoot ".." "docs" "issues"
$files = Get-ChildItem -Path $issueDir -Filter "*.md" |
    Where-Object { $_.Name -ne "README.md" } |
    Sort-Object Name

if (-not $files) { throw "No issue files found in $issueDir" }

$repoArgs = @()
if ($Repo) { $repoArgs = @("--repo", $Repo) }

$labelSet = New-Object System.Collections.Generic.HashSet[string]
foreach ($file in $files) {
    $text = Get-Content -Path $file.FullName -Raw
    if ($text -match '(?s)^---\s*\r?\n(.*?)\r?\n---\s*\r?\n(.*)$') {
        $front = $Matches[1]
        $body = $Matches[2].Trim()
    } else {
        Write-Warning "Skipping $($file.Name): no front matter"
        continue
    }

    $title = ($front -split "`n" | Where-Object { $_ -match '^title:' } | Select-Object -First 1) -replace '^title:\s*', ''
    $labels = (($front -split "`n" | Where-Object { $_ -match '^labels:' } | Select-Object -First 1) -replace '^labels:\s*', '') -split ',' |
        ForEach-Object { $_.Trim() } |
        Where-Object { $_ }

    if (-not $title) {
        Write-Warning "Skipping $($file.Name): no title"
        continue
    }

    foreach ($label in $labels) { [void]$labelSet.Add($label) }

    if ($DryRun) {
        Write-Output ("would create: {0}  [{1}]" -f $title, ($labels -join ", "))
        continue
    }

    if ($EnsureLabels) {
        foreach ($label in $labels) {
            gh label create $label --description "Filed from the remediation branch" --color "ededed" @repoArgs 2>$null | Out-Null
        }
        $EnsureLabels = $false  # once is enough
    }

    $labelArgs = @()
    foreach ($label in $labels) { $labelArgs += @("--label", $label) }

    Write-Output ("creating: {0}" -f $title)
    gh issue create @repoArgs --title $title --body $body @labelArgs | Write-Output
}

if ($DryRun) {
    Write-Output ""
    Write-Output ("labels referenced: {0}" -f (($labelSet | Sort-Object) -join ", "))
}
