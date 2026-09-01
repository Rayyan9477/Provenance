#Requires -Version 5.1
<#
.SYNOPSIS
    Provenance — Phase 0 capability probes PB-1 .. PB-6 (Windows / PowerShell).

.DESCRIPTION
    Runs the six Phase 0 capability probes defined in docs/ops/41_RUNBOOK.md section 3,
    which consume the eleven SQL preflight probes P1..P11 defined in
    docs/specs/10_DATABASE_DDL.md section 1.

    Every probe prints an unmistakable RESULT line and, on failure, the predetermined
    fallback already frozen in docs/CANONICAL_DECISIONS.md ("Phase 0 verification
    decisions"). A probe is not an invitation to redesign the product; it selects
    between paths that are already written.

    The run does NOT stop on the first failure. All six results are produced in one
    pass. Only PB-4 is phase-stopping (it stops Phase 11, per CANONICAL_DECISIONS.md).

    Transcripts written (paths are read verbatim by gate G-0):
        ops/cluster-probe.txt                  P1..P11 + PB-1, PB-2, PB-3
        ops/grant-probe.txt                    PB-4
        ops/bedrock-probe.txt                  PB-5
        ops/restore-probe.txt                  PB-6
        ops/decisions/VECTOR_INDEX_VARIANT.md  exactly one line matching ^VARIANT: (A|B|C)$
        ops/probes/PROBE_LEDGER.md             the section 3.7 ledger, pre-filled

.PARAMETER DbUrlEnvVar
    Name of the environment variable holding the bootstrap SQL connection string.
    The value is never printed, never echoed, and is scrubbed out of every transcript.

.EXAMPLE
    # Do NOT type the password into the command line. PSReadLine writes every console line to
    # $env:APPDATA\Microsoft\Windows\PowerShell\PSReadLine\ConsoleHost_history.txt, and its
    # sensitive-line filter matches only password/secret/token/apikey/asplaintext -- none of
    # which appear in a postgresql://user:pw@host literal. Prompt for it instead:
    $pw = Read-Host 'CockroachDB SQL password' -AsSecureString
    $env:PV_PROBE_DB_URL = 'postgresql://<user>:' +
        [Runtime.InteropServices.Marshal]::PtrToStringAuto(
            [Runtime.InteropServices.Marshal]::SecureStringToBSTR($pw)) +
        '@<cluster-host>.cockroachlabs.cloud:26257/defaultdb?sslmode=verify-full'
    .\ops\probes\phase0-probe.ps1
    Remove-Item Env:\PV_PROBE_DB_URL

.NOTES
    Exit codes: 0 = all six probes PASS.  1 = at least one probe FAILED or did not run.
                2 = PB-4 FAILED (phase-stopping).
    There is deliberately no separate "preflight failed" code: a preflight that cannot start
    leaves every probe at "not run", which is exit 1 and is already visible in the summary.
#>

[CmdletBinding()]
param(
    [string]  $DbUrlEnvVar          = 'PV_PROBE_DB_URL',
    [string]  $MigratorUrlEnvVar    = 'PV_PROBE_MIGRATOR_URL',
    [string]  $RepoRoot,
    [string]  $CaCertPath           = (Join-Path $env:APPDATA 'postgresql\root.crt'),
    [string]  $ClusterId            = '4023638b-52be-42bd-9677-d3611c613477',
    [string]  $Region               = 'us-east-1',
    [string]  $AppDatabase          = 'provenance',
    [string]  $CloneDatabase        = 'provenance_scn_01',
    [string]  $PythonExe            = 'python',
    [int]     $RestoreBudgetSeconds = 90,
    [int]     $ProbeRowCount        = 500,
    [switch]  $SkipSql,
    [switch]  $SkipAws,
    [switch]  $KeepProbeObjects,
    # Required to overwrite Phase 0 evidence that already exists. Without it the
    # script refuses to start rather than replacing a PASS transcript, or a
    # frozen VARIANT decision, with the output of a run that reached nothing.
    # See section 4 and D-00-005.
    [switch]  $Force
)

$ErrorActionPreference = 'Continue'   # never abort the run; each probe has a frozen fallback
$ProgressPreference    = 'SilentlyContinue'

# =============================================================================
# 0. Secret scrubbing. Nothing password-shaped may reach a transcript or a console.
# =============================================================================

$script:Secrets = New-Object System.Collections.Generic.List[string]

function Add-SecretLiteral {
    param([string] $Value)
    if ([string]::IsNullOrWhiteSpace($Value)) { return }
    if ($Value.Length -lt 4) { return }
    foreach ($form in @($Value, [Uri]::EscapeDataString($Value), [Uri]::UnescapeDataString($Value))) {
        if ($form -and $form.Length -ge 4 -and -not $script:Secrets.Contains($form)) {
            $script:Secrets.Add($form)
        }
    }
}

function Protect-Text {
    param([string] $Text)
    if ($null -eq $Text) { return '' }
    $out = $Text
    # 1. Known literals first (longest first, so a substring never shadows a longer secret).
    foreach ($s in ($script:Secrets | Sort-Object -Property Length -Descending)) {
        $out = $out.Replace($s, '[REDACTED]')
    }
    # 2. Shape-based defence in depth, for anything we did not know about.
    # BOTH halves of the userinfo, not just the password. Some CockroachDB Cloud
    # connection strings carry the token in the USER field, so redacting only the
    # password leaks on exactly the shape that matters -- which is why
    # tools/scrub.py redacts both and tools/tests/test_scrub.py
    # ::test_the_sql_role_name_does_not_survive_a_connection_url asserts it.
    # This scrubber writes the committed ops/*.txt transcripts, so the two must
    # not disagree. (D-00-005.)
    $out = [regex]::Replace($out, '(?i)(postgres(?:ql)?)://[^:/\s@]+:[^@\s]*@', '$1://[REDACTED-USER]:[REDACTED]@')
    # Symmetric signing keys named in 40_INFRA_IAC.md S12. Possession is forgery
    # of a capability token or a pagination cursor.
    $out = [regex]::Replace($out, '\b([A-Z][A-Z0-9_]*(?:HMAC_KEY|SIGNING_KEY|PRIVATE_KEY|SECRET_KEY|_SECRET|_TOKEN|_PASSWORD))\s*[:=]\s*(?![\s$<{])[^\s&;,)]+', '$1=[REDACTED]')
    $out = [regex]::Replace($out, '(?i)\b(password|pwd|passwd|pgpassword)\s*=\s*''?[^\s''&;,)]+', '$1=[REDACTED]')
    $out = [regex]::Replace($out, '(?i)\bPASSWORD\s+''[^'']*''', "PASSWORD '[REDACTED]'")
    $out = [regex]::Replace($out, '\b(?:AKIA|ASIA)[0-9A-Z]{16}\b', '[REDACTED-AWS-ACCESS-KEY]')
    $out = [regex]::Replace($out, '(?i)(aws_secret_access_key\s*[=:]\s*)\S+', '$1[REDACTED]')
    $out = [regex]::Replace($out, '(?i)(aws_session_token\s*[=:]\s*)\S+', '$1[REDACTED]')
    $out = [regex]::Replace($out, '(?i)(authorization:\s*bearer\s+)\S+', '$1[REDACTED]')
    # 3. AWS account identity. ops/ is committed and the repository becomes PUBLIC at S1/G0.2,
    #    and gitleaks does not flag a bare account id. Mask the id inside an ARN first, so the
    #    ARN keeps its shape and stays diagnosable, then mask any remaining bare 12-digit id.
    $out = [regex]::Replace($out, '(arn:aws[a-z\-]*:[^:\s]*:[^:\s]*:)\d{12}(:)', '${1}[REDACTED-AWS-ACCOUNT]${2}')
    $out = [regex]::Replace($out, '(?<![0-9A-Za-z\-])\d{12}(?![0-9A-Za-z\-])', '[REDACTED-AWS-ACCOUNT]')
    return $out
}

function Get-DisplayUrl {
    param([string] $Url)
    if (-not $Url) { return '<unset>' }
    return (Protect-Text $Url)
}

# =============================================================================
# 1. Transcript writers. All disk writes go through here, all of them scrubbed.
# =============================================================================

$script:Utf8NoBom = New-Object System.Text.UTF8Encoding($false)
$script:MaxLine   = 1200

function Format-TranscriptLines {
    param([string] $Text)
    $safe  = Protect-Text $Text
    $lines = $safe -split "`r`n|`n|`r"
    $outLines = @()
    foreach ($l in $lines) {
        if ($l.Length -gt $script:MaxLine) {
            $outLines += ($l.Substring(0, $script:MaxLine) +
                          " ...[line truncated, $($l.Length - $script:MaxLine) more chars]")
        } else {
            $outLines += $l
        }
    }
    return $outLines
}

function Write-Probe {
    param(
        [string] $Text,
        [string] $Path,
        [switch] $NoConsole,
        [string] $Color = 'Gray'
    )
    $lines = Format-TranscriptLines $Text
    $blob  = ($lines -join "`n") + "`n"
    if ($Path) { [System.IO.File]::AppendAllText($Path, $blob, $script:Utf8NoBom) }
    if (-not $NoConsole) {
        foreach ($l in $lines) { Write-Host $l -ForegroundColor $Color }
    }
}

function Write-Banner {
    param([string] $Title, [string] $Path, [string] $Color = 'Cyan')
    $bar = '=' * 78
    Write-Probe -Text "`n$bar`n$Title`n$bar" -Path $Path -Color $Color
}

function Write-Result {
    param([string] $Label, [bool] $Pass, [string] $Detail, [string] $Path)
    if ($Pass) { $verdict = 'PASS' ; $color = 'Green' } else { $verdict = 'FAIL' ; $color = 'Red' }
    $line = ("RESULT {0}: {1}" -f $Label, $verdict)
    if ($Detail) { $line += "  -- $Detail" }
    Write-Probe -Text $line -Path $Path -Color $color
}

function Write-TextFile {
    param([string] $Path, [string] $Text)
    $lines = Format-TranscriptLines $Text
    [System.IO.File]::WriteAllText($Path, (($lines -join "`n") + "`n"), $script:Utf8NoBom)
}

# =============================================================================
# 2. Connection-string handling. Parsed by hand: System.Uri re-encodes and would
#    corrupt a Windows sslrootcert path.
# =============================================================================

$script:UrlPattern = '^(?<scheme>postgres(?:ql)?)://(?<user>[^:@/?#]+)(?::(?<pass>[^@]*))?@(?<hostport>[^/?#]+)(?:/(?<db>[^?#]*))?(?:\?(?<query>[^#]*))?$'

function Split-DbUrl {
    param([string] $Url)
    $m = [regex]::Match($Url, $script:UrlPattern)
    if (-not $m.Success) { return $null }
    $q = New-Object 'System.Collections.Specialized.OrderedDictionary'
    if ($m.Groups['query'].Success -and $m.Groups['query'].Value) {
        foreach ($pair in ($m.Groups['query'].Value -split '&')) {
            if (-not $pair) { continue }
            $i = $pair.IndexOf('=')
            if ($i -lt 0) { $q[$pair] = '' } else { $q[$pair.Substring(0, $i)] = $pair.Substring($i + 1) }
        }
    }
    return [pscustomobject]@{
        Scheme   = $m.Groups['scheme'].Value
        User     = $m.Groups['user'].Value
        Password = $m.Groups['pass'].Value
        HostPort = $m.Groups['hostport'].Value
        Database = $m.Groups['db'].Value
        Query    = $q
    }
}

function Join-DbUrl {
    param([psobject] $Parts, [string] $Database, [string] $User, [string] $Password)
    $u  = if ($User) { $User } else { $Parts.User }
    $p  = if ($PSBoundParameters.ContainsKey('Password')) { $Password } else { $Parts.Password }
    $db = if ($Database) { $Database } else { $Parts.Database }
    $sb = New-Object System.Text.StringBuilder
    [void]$sb.Append($Parts.Scheme).Append('://').Append($u)
    if ($p) { [void]$sb.Append(':').Append($p) }
    [void]$sb.Append('@').Append($Parts.HostPort).Append('/').Append($db)
    if ($Parts.Query.Count -gt 0) {
        $pairs = @()
        foreach ($k in $Parts.Query.Keys) { $pairs += ("{0}={1}" -f $k, $Parts.Query[$k]) }
        [void]$sb.Append('?').Append(($pairs -join '&'))
    }
    return $sb.ToString()
}

# =============================================================================
# 3. Process helpers. PowerShell 5.1 wraps native stderr in ErrorRecord objects,
#    so every captured stream is forced back to plain strings and only
#    $LASTEXITCODE is trusted.
# =============================================================================

function Invoke-Native {
    param([string] $Exe, [string[]] $Arguments)
    $global:LASTEXITCODE = 0
    $raw  = & $Exe @Arguments 2>&1
    $code = $LASTEXITCODE
    $text = (@($raw) | ForEach-Object { $_.ToString() }) -join "`n"
    return [pscustomobject]@{ ExitCode = $code; Text = $text; Ok = ($code -eq 0) }
}

function Invoke-Crdb {
    param(
        [Parameter(Mandatory = $true)][string]   $Url,
        [Parameter(Mandatory = $true)][string[]] $Statements,
        [string] $Format   = 'csv',
        [string] $Database
    )
    if (-not $script:CockroachExe) {
        return [pscustomobject]@{ ExitCode = 127; Text = 'cockroach CLI not found on PATH'; Ok = $false }
    }
    $cmdArgs = @('sql', '--url', $Url, "--format=$Format")
    if ($Database) { $cmdArgs += @('--database', $Database) }
    foreach ($s in $Statements) { $cmdArgs += @('-e', $s) }
    try {
        return Invoke-Native -Exe $script:CockroachExe -Arguments $cmdArgs
    } catch {
        return [pscustomobject]@{ ExitCode = 126; Text = $_.Exception.Message; Ok = $false }
    }
}

function Invoke-CrdbTimed {
    param([string] $Url, [string[]] $Statements, [string] $Format = 'csv', [string] $Database)
    $sw = [System.Diagnostics.Stopwatch]::StartNew()
    $r  = Invoke-Crdb -Url $Url -Statements $Statements -Format $Format -Database $Database
    $sw.Stop()
    Add-Member -InputObject $r -NotePropertyName 'Seconds' -NotePropertyValue ([math]::Round($sw.Elapsed.TotalSeconds, 2)) -Force
    return $r
}

function Test-Tool {
    param([string] $Name)
    $c = Get-Command $Name -ErrorAction SilentlyContinue
    if ($c) { return $c.Source } else { return $null }
}

function New-ProbePassword {
    # 31 chars (28 random + one forced upper, lower and digit), alphanumeric only.
    # Alphanumeric avoids URL-encoding hazards in the connection string; 31 chars
    # clears the CockroachDB Cloud minimum comfortably.
    $alphabet = 'ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz23456789'
    $bytes    = New-Object 'System.Byte[]' 28
    $rng      = [System.Security.Cryptography.RandomNumberGenerator]::Create()
    $rng.GetBytes($bytes)
    $chars    = foreach ($b in $bytes) { $alphabet[$b % $alphabet.Length] }
    # Guarantee at least one upper, one lower and one digit.
    return (($chars -join '') + 'A' + 'a' + '7')
}

# =============================================================================
# 4. Preflight
# =============================================================================

if (-not $RepoRoot) { $RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path }

$OpsDir        = Join-Path $RepoRoot 'ops'
$DecisionsDir  = Join-Path $OpsDir   'decisions'
$ProbesDir     = Join-Path $OpsDir   'probes'
foreach ($d in @($OpsDir, $DecisionsDir, $ProbesDir)) {
    if (-not (Test-Path $d)) { New-Item -ItemType Directory -Force -Path $d | Out-Null }
}

$ClusterProbe = Join-Path $OpsDir 'cluster-probe.txt'
$GrantProbe   = Join-Path $OpsDir 'grant-probe.txt'
$BedrockProbe = Join-Path $OpsDir 'bedrock-probe.txt'
$RestoreProbe = Join-Path $OpsDir 'restore-probe.txt'
$VariantFile  = Join-Path $DecisionsDir 'VECTOR_INDEX_VARIANT.md'
# ONE ledger path, not two. `ops/probes/PROBE_LEDGER.md` and `ops/PROBE_LEDGER.md`
# both existed and carried contradictory verdicts; `.gitleaks.toml` allowlists
# only `ops/PROBE_LEDGER.md`, so that is the committed one and the generated
# ledger writes there. A generated ledger must not sit beside a curated one
# under the same name. (D-00-005.)
$LedgerFile   = Join-Path $OpsDir 'PROBE_LEDGER.md'

# --- evidence-file precondition -------------------------------------------------
# These six files are COMMITTED Phase 0 evidence. `G0.6` reads two of them
# verbatim and `ops/PROBE_LEDGER.md` is what a gate reviewer reads first.
#
# This block used to be an unconditional truncate-to-empty at script start,
# BEFORE any connectivity check. Running `make probe` on a machine without the
# cockroach CLI therefore destroyed a full P1..P11 transcript and rewrote the
# frozen `VARIANT: A` decision as "NO VARIANT SELECTED" -- from a run that had
# connected to nothing. `ops/` is untracked at this point in the build, so
# there is no `git checkout` recovery. That is D-00-005/D-00-006.
#
# The rule now: a probe run never opens an output file it cannot fill, and
# never silently replaces evidence. Regeneration is deliberate (-Force).
$EvidenceFiles = @($ClusterProbe, $GrantProbe, $BedrockProbe, $RestoreProbe, $VariantFile, $LedgerFile)
$ExistingEvidence = @($EvidenceFiles | Where-Object {
    (Test-Path -LiteralPath $_) -and ((Get-Item -LiteralPath $_).Length -gt 0)
})
if ($ExistingEvidence.Count -gt 0 -and -not $Force) {
    Write-Host ''
    Write-Host 'REFUSING TO RUN: Phase 0 evidence already exists and would be overwritten.' -ForegroundColor Red
    foreach ($f in $ExistingEvidence) { Write-Host "  $f" -ForegroundColor Red }
    Write-Host ''
    Write-Host 'These files are committed evidence that G0.6 and the gate reviewer read.' -ForegroundColor Yellow
    Write-Host 'A re-run that cannot reach the cluster would replace a PASS transcript with' -ForegroundColor Yellow
    Write-Host 'a "not run" stub and rewrite ops/decisions/VECTOR_INDEX_VARIANT.md, and ops/' -ForegroundColor Yellow
    Write-Host 'is untracked so nothing restores it.' -ForegroundColor Yellow
    Write-Host ''
    Write-Host 'To regenerate deliberately:  .\ops\probes\phase0-probe.ps1 -Force' -ForegroundColor Yellow
    Write-Host 'Back the files up first if the current run may not reach the cluster.' -ForegroundColor Yellow
    Write-Host ''
    exit 1
}
foreach ($f in @($ClusterProbe, $GrantProbe, $BedrockProbe, $RestoreProbe)) {
    [System.IO.File]::WriteAllText($f, '', $script:Utf8NoBom)
}

$RunStamp = (Get-Date).ToUniversalTime().ToString('yyyy-MM-ddTHH:mm:ssZ')
$TempDir  = Join-Path $env:TEMP ("pv-phase0-" + [guid]::NewGuid().ToString('N').Substring(0, 12))
New-Item -ItemType Directory -Force -Path $TempDir | Out-Null

Write-Host ''
Write-Host '###############################################################################' -ForegroundColor Cyan
Write-Host '#  Provenance - Phase 0 capability probes PB-1 .. PB-6                        #' -ForegroundColor Cyan
Write-Host '#  Authority: docs/ops/41_RUNBOOK.md S3, docs/specs/10_DATABASE_DDL.md S1     #' -ForegroundColor Cyan
Write-Host '#  Fallbacks: docs/CANONICAL_DECISIONS.md "Phase 0 verification decisions"    #' -ForegroundColor Cyan
Write-Host '###############################################################################' -ForegroundColor Cyan
Write-Host ''
Write-Host "repo root : $RepoRoot"
Write-Host "run at    : $RunStamp"
Write-Host ''

# --- toolchain -----------------------------------------------------------------
$script:CockroachExe = Test-Tool 'cockroach'
$script:AwsExe       = Test-Tool 'aws'
$script:PythonPath   = Test-Tool $PythonExe

$toolLines = @()
$toolLines += "cockroach : $(if ($script:CockroachExe) { $script:CockroachExe } else { 'NOT FOUND on PATH' })"
$toolLines += "aws       : $(if ($script:AwsExe)       { $script:AwsExe }       else { 'NOT FOUND on PATH' })"
$toolLines += "python    : $(if ($script:PythonPath)   { $script:PythonPath }   else { 'NOT FOUND on PATH' })"
Write-Host ($toolLines -join "`n") -ForegroundColor Gray
Write-Host ''

if (-not $script:CockroachExe -and -not $SkipSql) {
    Write-Host 'The cockroach CLI is required for PB-1, PB-2, PB-3, PB-4 and PB-6.' -ForegroundColor Yellow
    Write-Host 'Install it (PowerShell, one line, no admin needed):' -ForegroundColor Yellow
    Write-Host '  $ProgressPreference = "SilentlyContinue"; Invoke-WebRequest -Uri "https://binaries.cockroachdb.com/cockroach-v25.3.0.windows-6.2-amd64.zip" -OutFile "$env:TEMP\crdb.zip"; Expand-Archive "$env:TEMP\crdb.zip" -DestinationPath "$env:LOCALAPPDATA\cockroach" -Force' -ForegroundColor Yellow
    Write-Host '  then add the extracted folder to PATH and reopen the shell.' -ForegroundColor Yellow
    Write-Host 'Continuing so that PB-5 (AWS) still produces a result.' -ForegroundColor Yellow
    Write-Host ''
}

# --- CA certificate -------------------------------------------------------------
$CaCertPresent = Test-Path -LiteralPath $CaCertPath
$CaFetchCmd    = 'New-Item -ItemType Directory -Force -Path "$env:APPDATA\postgresql" | Out-Null; ' +
                 'Invoke-WebRequest -Uri "https://cockroachlabs.cloud/clusters/' + $ClusterId + '/cert" ' +
                 '-OutFile "$env:APPDATA\postgresql\root.crt"'
if (-not $CaCertPresent) {
    Write-Host "CA certificate NOT FOUND at: $CaCertPath" -ForegroundColor Yellow
    Write-Host 'sslmode=verify-full cannot succeed without it. Fetch it with exactly this command:' -ForegroundColor Yellow
    Write-Host "  $CaFetchCmd" -ForegroundColor Yellow
    Write-Host 'Then re-run this script. Continuing so AWS probes still report.' -ForegroundColor Yellow
    Write-Host ''
} else {
    Write-Host "CA certificate: $CaCertPath (present)" -ForegroundColor Gray
}

# --- connection string ----------------------------------------------------------
$RawUrl   = [Environment]::GetEnvironmentVariable($DbUrlEnvVar)
$MigUrl   = [Environment]::GetEnvironmentVariable($MigratorUrlEnvVar)
$UrlParts = $null
$SqlReady = $false

if ([string]::IsNullOrWhiteSpace($RawUrl)) {
    Write-Host "Environment variable `$env:$DbUrlEnvVar is not set." -ForegroundColor Yellow
    Write-Host 'Set it in YOUR shell. Do NOT paste the password as a literal: PSReadLine saves every' -ForegroundColor Yellow
    Write-Host 'console line to ConsoleHost_history.txt, and its sensitive-line filter does not match' -ForegroundColor Yellow
    Write-Host 'a postgresql://user:pw@host literal. Prompt for it instead:' -ForegroundColor Yellow
    Write-Host "  `$pw = Read-Host 'CockroachDB SQL password' -AsSecureString" -ForegroundColor Yellow
    Write-Host "  `$env:$DbUrlEnvVar = 'postgresql://<user>:' + [Runtime.InteropServices.Marshal]::PtrToStringAuto([Runtime.InteropServices.Marshal]::SecureStringToBSTR(`$pw)) + '@<cluster-host>.cockroachlabs.cloud:26257/defaultdb?sslmode=verify-full'" -ForegroundColor Yellow
    Write-Host "  # when you are done:  Remove-Item Env:\$DbUrlEnvVar" -ForegroundColor Yellow
    Write-Host ''
} else {
    $UrlParts = Split-DbUrl $RawUrl
    if (-not $UrlParts) {
        Write-Host "The value of `$env:$DbUrlEnvVar does not parse as a PostgreSQL URL. Not printing it." -ForegroundColor Red
    } else {
        Add-SecretLiteral $UrlParts.Password
        if ($MigUrl) {
            $mp = Split-DbUrl $MigUrl
            if ($mp) { Add-SecretLiteral $mp.Password }
        }
        # Force TLS posture and a bounded connect timeout onto every derived URL.
        $UrlParts.Query['sslmode'] = 'verify-full'
        if ($CaCertPresent) {
            $UrlParts.Query['sslrootcert'] = ($CaCertPath -replace ' ', '%20')
        }
        $UrlParts.Query['connect_timeout'] = '15'
        $UrlParts.Query['application_name'] = 'provenance-phase0-probe'
        $SqlReady = $true
        Write-Host ("connection: {0}" -f (Get-DisplayUrl (Join-DbUrl -Parts $UrlParts -Database $UrlParts.Database))) -ForegroundColor Gray
    }
}

if ($SkipSql) { $SqlReady = $false }

# --- results ledger -------------------------------------------------------------
$R = [ordered]@{
    PB1 = [ordered]@{ Pass = $false; Note = 'not run' }
    PB2 = [ordered]@{ Pass = $false; Note = 'not run'; Variant = ''; Chosen = $false }
    PB3 = [ordered]@{ Pass = $false; Note = 'not run'; Stored = $false; Ttl = $false; Families = $false }
    PB4 = [ordered]@{ Pass = $false; Note = 'not run' }
    PB5 = [ordered]@{ Pass = $false; Note = 'not run'; Dims = 0; L2 = 0.0 }
    PB6 = [ordered]@{ Pass = $false; Note = 'not run'; Seconds = 0.0 }
}

$header = @"
== Provenance Phase 0 cluster probe ==
run_at              : $RunStamp
cluster_id          : $ClusterId
host                : $(if ($UrlParts) { $UrlParts.HostPort } else { 'n/a' })
application_database: $AppDatabase
tls                 : sslmode=verify-full, ca=$CaCertPath (present=$CaCertPresent)
authority           : docs/specs/10_DATABASE_DDL.md S1 (P1..P11); docs/ops/41_RUNBOOK.md S3 (PB-1..PB-3)
scrubbing           : every line below passed through the password scrubber before being written
note                : the cleanup step is headed "-- CLEANUP", not "-- P12", because G0.6 counts
                      lines beginning "-- P" and expects exactly 11
"@
Write-Probe -Text $header -Path $ClusterProbe -Color 'Gray'

# =============================================================================
# 5. Create the application database. The console connection string points at
#    defaultdb; the application database is "provenance" and does not exist yet.
# =============================================================================

$BaseUrl = $null
if ($SqlReady) {
    Write-Banner -Title 'PREFLIGHT - application database' -Path $ClusterProbe
    $bootstrapUrl = Join-DbUrl -Parts $UrlParts -Database $UrlParts.Database
    $mk = Invoke-Crdb -Url $bootstrapUrl -Statements @(
        "CREATE DATABASE IF NOT EXISTS $AppDatabase;",
        "CREATE DATABASE IF NOT EXISTS provenance_ci;",
        "SHOW DATABASES;"
    )
    Write-Probe -Text $mk.Text -Path $ClusterProbe
    if ($mk.Ok) {
        Write-Probe -Text "database '$AppDatabase' present (created if absent), 'provenance_ci' present" -Path $ClusterProbe -Color 'Green'
        $BaseUrl = Join-DbUrl -Parts $UrlParts -Database $AppDatabase
    } else {
        Write-Probe -Text "Could not create or list databases (exit $($mk.ExitCode)). All SQL probes will fail. Check the CA cert, the password, and network egress on 26257." -Path $ClusterProbe -Color 'Red'
        $SqlReady = $false
    }
}

# =============================================================================
# 6. PB-1  --  Vector indexing enabled on a CockroachDB Cloud Basic cluster
#              Consumes P1 and P2. THE LIKELIEST PROBE TO FAIL.
# =============================================================================

$pb1State = 'NOT_RUN'
if ($SqlReady) {
    Write-Banner -Title 'PB-1  Vector indexing enabled on a Basic cluster  (P1, P2)' -Path $ClusterProbe

    Write-Probe -Text "`n-- P1. Build and logical cluster version." -Path $ClusterProbe -Color 'White'
    $p1 = Invoke-Crdb -Url $BaseUrl -Statements @('SELECT version();', 'SHOW CLUSTER SETTING version;')
    Write-Probe -Text $p1.Text -Path $ClusterProbe
    $p1Pass = ($p1.Ok -and $p1.Text -match 'CockroachDB')
    Write-Result -Label 'P1' -Pass $p1Pass -Detail 'cluster answers and reports a build' -Path $ClusterProbe

    Write-Probe -Text "`n-- P2. Vector-related cluster settings, and whether they are enabled. [PB-1]" -Path $ClusterProbe -Color 'White'
    $p2a = Invoke-Crdb -Url $BaseUrl -Statements @("SELECT variable, value FROM [SHOW CLUSTER SETTINGS] WHERE variable ILIKE '%vector%';")
    Write-Probe -Text $p2a.Text -Path $ClusterProbe

    $p2b = Invoke-Crdb -Url $BaseUrl -Statements @('SET CLUSTER SETTING feature.vector_index.enabled = true;')
    Write-Probe -Text $p2b.Text -Path $ClusterProbe

    $p2c = Invoke-Crdb -Url $BaseUrl -Statements @('SHOW CLUSTER SETTING feature.vector_index.enabled;')
    Write-Probe -Text $p2c.Text -Path $ClusterProbe

    $settingListed  = ($p2a.Text -match 'feature\.vector_index\.enabled')
    $showsTrue      = ($p2c.Ok -and $p2c.Text -match '(?m)^\s*(true|t)\s*$')
    $privilegeDenie = ($p2b.Text -match '(?i)MODIFY(SQL)?CLUSTERSETTING' -or $p2b.Text -match '(?i)insufficient privilege')
    $unknownSetting = ($p2b.Text -match '(?i)unknown cluster setting' -or $p2c.Text -match '(?i)unknown cluster setting')

    if     ($showsTrue -and $p2b.Ok) { $pb1State = 'ENABLED_BY_PROBE' }
    elseif ($showsTrue)              { $pb1State = 'ALREADY_ENABLED' }
    elseif ($unknownSetting)         { $pb1State = 'SETTING_ABSENT' }
    elseif ($privilegeDenie)         { $pb1State = 'PRIVILEGE_DENIED' }
    else                             { $pb1State = 'ERROR' }

    Write-Probe -Text "PB-1 state: $pb1State  (setting_listed=$settingListed shows_true=$showsTrue)" -Path $ClusterProbe -Color 'White'
    Write-Probe -Text "PB-1 interim only. Final PB-1 verdict is decided after PB-2, because a cluster where the setting is absent BUT CREATE VECTOR INDEX succeeds is also a pass -- there is no gate to open. These are two different facts and both are recorded." -Path $ClusterProbe -Color 'Gray'

    if ($MigUrl) {
        Write-Probe -Text "`nPB-1 second identity: re-running the SET as the role in `$env:$MigratorUrlEnvVar (pv_migrator), because the bootstrap user may hold privileges pv_migrator does not." -Path $ClusterProbe -Color 'White'
        $mparts = Split-DbUrl $MigUrl
        if ($mparts) {
            $mparts.Query['sslmode'] = 'verify-full'
            if ($CaCertPresent) { $mparts.Query['sslrootcert'] = ($CaCertPath -replace ' ', '%20') }
            $mparts.Query['connect_timeout'] = '15'
            $mig = Invoke-Crdb -Url (Join-DbUrl -Parts $mparts -Database $AppDatabase) -Statements @(
                'SET CLUSTER SETTING feature.vector_index.enabled = true;',
                'SHOW CLUSTER SETTING feature.vector_index.enabled;'
            )
            Write-Probe -Text $mig.Text -Path $ClusterProbe
            Write-Probe -Text "pv_migrator can set the vector-index cluster setting: $($mig.Ok)" -Path $ClusterProbe -Color 'White'
        }
    } else {
        Write-Probe -Text "PB-1 note: `$env:$MigratorUrlEnvVar is unset, so PB-1 was answered only for the bootstrap SQL user. The SQL roles pv_migrator, pv_app_reader_writer, pv_kernel_writer and pv_agent_reader do not exist until migration 0001/0008. Re-assert PB-1 as pv_migrator once the roles exist." -Path $ClusterProbe -Color 'Yellow'
    }
}

# =============================================================================
# 7. PB-2  --  Prefix-column cosine vector index, created AND CHOSEN
#              Consumes P3, P4, P5, P6, P7, P8 plus the EXPLAIN proof.
# =============================================================================

$selectedVariant = ''
$selectedIndex   = ''
$variantLog      = @()
$explainText     = ''
$indexChosen     = $false
$p8Pass          = $false

if ($SqlReady) {
    Write-Banner -Title 'PB-2  Prefix cosine vector index, created and CHOSEN  (P3..P8)' -Path $ClusterProbe

    Write-Probe -Text "`n-- P3. Session-level ANN tuning knob: vector_search_beam_size (default 32)." -Path $ClusterProbe -Color 'White'
    $p3 = Invoke-Crdb -Url $BaseUrl -Statements @('SHOW vector_search_beam_size;')
    Write-Probe -Text $p3.Text -Path $ClusterProbe
    Write-Result -Label 'P3' -Pass ($p3.Ok) -Detail 'if this errors, the build predates tunable beams (not fatal)' -Path $ClusterProbe

    Write-Probe -Text "`n-- P4. Does the VECTOR type exist with a fixed dimension?" -Path $ClusterProbe -Color 'White'
$p4Sql = @'
CREATE TABLE IF NOT EXISTS _pv_probe (
    id UUID NOT NULL PRIMARY KEY,
    k  UUID NOT NULL,
    v  VECTOR(1024)
);
'@
    $p4 = Invoke-Crdb -Url $BaseUrl -Statements @($p4Sql)
    Write-Probe -Text $p4.Text -Path $ClusterProbe
    Write-Result -Label 'P4' -Pass ($p4.Ok) -Detail 'VECTOR(1024) column type' -Path $ClusterProbe

    Write-Probe -Text "`n-- P5. Which distance operators parse (L2, cosine, negative inner product)?" -Path $ClusterProbe -Color 'White'
    $opStatements = [ordered]@{
        'l2_distance       <->' = "SELECT '[1,2,3]'::VECTOR(3) <-> '[3,2,1]'::VECTOR(3) AS l2_distance;"
        'cosine_distance   <=>' = "SELECT '[1,2,3]'::VECTOR(3) <=> '[3,2,1]'::VECTOR(3) AS cosine_distance;"
        'neg_inner_product <#>' = "SELECT '[1,2,3]'::VECTOR(3) <#> '[3,2,1]'::VECTOR(3) AS neg_inner_product;"
    }
    $cosineOpOk = $false
    foreach ($k in $opStatements.Keys) {
        $r = Invoke-Crdb -Url $BaseUrl -Statements @($opStatements[$k])
        Write-Probe -Text ("{0,-24} -> {1}" -f $k, ($(if ($r.Ok) { 'OK  ' + (($r.Text -split "`n") | Select-Object -Last 1) } else { 'ERROR ' + $r.Text }))) -Path $ClusterProbe
        if ($k -like 'cosine*' -and $r.Ok) { $cosineOpOk = $true }
    }
    Write-Result -Label 'P5' -Pass $cosineOpOk -Detail 'the cosine operator <=> is the one that decides variant A/B vs variant C' -Path $ClusterProbe

    Write-Probe -Text "`n-- P6. Which index syntax and operator class is accepted? Keep the FIRST that succeeds." -Path $ClusterProbe -Color 'White'
    $attempts = @(
        @{ Letter = 'A'; Index = '_pv_probe_a'; Sql = 'CREATE VECTOR INDEX _pv_probe_a ON _pv_probe (k, v vector_cosine_ops);';             Desc = 'CREATE VECTOR INDEX, cosine opclass  (DDL S5.1)' },
        @{ Letter = 'B'; Index = '_pv_probe_b'; Sql = 'CREATE INDEX _pv_probe_b ON _pv_probe USING cspann (k, v vector_cosine_ops);';       Desc = 'access-method syntax, cosine opclass (DDL S5.2)' },
        @{ Letter = 'C'; Index = '_pv_probe_c'; Sql = 'CREATE VECTOR INDEX _pv_probe_c ON _pv_probe (k, v);';                               Desc = 'default (L2) opclass                  (DDL S5.3)' }
    )
    foreach ($a in $attempts) {
        if ($selectedVariant) {
            $variantLog += ("variant {0}: not attempted (variant {1} already succeeded; the FIRST success wins)" -f $a.Letter, $selectedVariant)
            continue
        }
        $r = Invoke-Crdb -Url $BaseUrl -Statements @($a.Sql)
        if ($r.Ok) {
            $selectedVariant = $a.Letter
            $selectedIndex   = $a.Index
            $variantLog += ("variant {0}: SUCCESS  {1}" -f $a.Letter, $a.Desc)
        } else {
            $variantLog += ("variant {0}: FAILED   {1}  -> {2}" -f $a.Letter, $a.Desc, (($r.Text -split "`n") | Where-Object { $_ -match 'ERROR|error' } | Select-Object -First 1))
        }
    }
    Write-Probe -Text ($variantLog -join "`n") -Path $ClusterProbe
    Write-Result -Label 'P6' -Pass ([bool]$selectedVariant) -Detail ("selected variant: {0}" -f $(if ($selectedVariant) { $selectedVariant } else { 'NONE' })) -Path $ClusterProbe

    Write-Probe -Text "`n-- P7. Confirm what was actually created, including access method and opclass." -Path $ClusterProbe -Color 'White'
    $p7 = Invoke-Crdb -Url $BaseUrl -Statements @('SHOW INDEXES FROM _pv_probe;', 'SELECT create_statement FROM [SHOW CREATE TABLE _pv_probe];') -Format 'table'
    Write-Probe -Text $p7.Text -Path $ClusterProbe
    $prefixFirst = ($selectedIndex -and $p7.Text -match [regex]::Escape($selectedIndex))
    Write-Result -Label 'P7' -Pass ($p7.Ok -and $prefixFirst) -Detail 'the index exists and the indexed columns begin with the k prefix' -Path $ClusterProbe

    Write-Probe -Text "`n-- P8. Confirm a multi-column prefix is permitted (needed only for optional index variant R)." -Path $ClusterProbe -Color 'White'
$p8Create = @'
CREATE TABLE IF NOT EXISTS _pv_probe2 (
    id UUID NOT NULL PRIMARY KEY,
    k  UUID NOT NULL,
    ok BOOL NOT NULL,
    v  VECTOR(1024)
);
'@
    $null = Invoke-Crdb -Url $BaseUrl -Statements @($p8Create)
    $p8Sql = if ($selectedVariant -eq 'C') {
        'CREATE VECTOR INDEX _pv_probe2_a ON _pv_probe2 (k, ok, v);'
    } elseif ($selectedVariant -eq 'B') {
        'CREATE INDEX _pv_probe2_a ON _pv_probe2 USING cspann (k, ok, v vector_cosine_ops);'
    } else {
        'CREATE VECTOR INDEX _pv_probe2_a ON _pv_probe2 (k, ok, v vector_cosine_ops);'
    }
    $p8 = Invoke-Crdb -Url $BaseUrl -Statements @($p8Sql)
    Write-Probe -Text $p8.Text -Path $ClusterProbe
    $p8Pass = $p8.Ok
    Write-Result -Label 'P8' -Pass $p8Pass -Detail 'multi-column prefix (user_id, is_retrieval_eligible, embedding)' -Path $ClusterProbe
    if (-not $p8Pass) {
        Write-Probe -Text "FALLBACK for P8: skip optional index variant R (evidence_embedding_ann_active_idx). Retraction filtering falls back to over-fetch-then-filter, which is the default path anyway: k_raw = greatest(40, 4 * k_final), k_final = 20." -Path $ClusterProbe -Color 'Yellow'
    }

    # ---- the part that actually matters: is the index CHOSEN? -------------------
    Write-Probe -Text "`n-- INDEX-CHOSEN PROOF [PB-2]. A full scan while the index exists is a FAILURE even when the results are correct (G6.2)." -Path $ClusterProbe -Color 'White'
    if ($selectedVariant) {
        # Correlated scalar subquery: g.i is referenced inside, so the optimizer
        # cannot hoist it and every row genuinely gets a distinct vector.
        $insertSql = @"
INSERT INTO _pv_probe (id, k, v)
SELECT gen_random_uuid(),
       '00000000-0000-4000-8000-000000000001',
       ('[' || (SELECT string_agg((random() + g.i)::STRING, ',')
                FROM generate_series(1, 1024)) || ']')::VECTOR(1024)
FROM generate_series(1, $ProbeRowCount) AS g(i);
"@
        $ins = Invoke-CrdbTimed -Url $BaseUrl -Statements @($insertSql)
        Write-Probe -Text ("inserted {0} probe vectors in {1}s (exit {2})" -f $ProbeRowCount, $ins.Seconds, $ins.ExitCode) -Path $ClusterProbe
        if (-not $ins.Ok) { Write-Probe -Text $ins.Text -Path $ClusterProbe }

        # Variant C indexes with the default L2 opclass, so the ranked block must
        # order by <-> for the index to be a candidate. Ordering by <=> against an
        # L2 index is a guaranteed full scan and would misreport PB-2 as a failure.
        $distOp = if ($selectedVariant -eq 'C') { '<->' } else { '<=>' }
        $explainSql = @"
EXPLAIN (VERBOSE)
SELECT id FROM _pv_probe
WHERE k = '00000000-0000-4000-8000-000000000001'
ORDER BY v $distOp (SELECT v FROM _pv_probe LIMIT 1)
LIMIT 40;
"@
        $ex = Invoke-Crdb -Url $BaseUrl -Statements @($explainSql) -Format 'table'
        $explainText = $ex.Text
        Write-Probe -Text $explainText -Path $ClusterProbe

        $namesIndex = ($explainText -match [regex]::Escape($selectedIndex))
        $fullScan   = ($explainText -match '(?i)FULL SCAN')
        $indexChosen = ($ex.Ok -and $namesIndex -and -not $fullScan)

        if (-not $indexChosen -and $ex.Ok) {
            Write-Probe -Text "`nEXPLAIN did not select the index with a subquery as the query vector. Retrying with a literal query vector, which distinguishes 'the planner cannot use a subquery here' from 'the index is unusable'." -Path $ClusterProbe -Color 'Yellow'
            $lit = '[' + ((1..1024 | ForEach-Object { '0.031' }) -join ',') + ']'
            $explainSql2 = @"
EXPLAIN (VERBOSE)
SELECT id FROM _pv_probe
WHERE k = '00000000-0000-4000-8000-000000000001'
ORDER BY v $distOp '$lit'::VECTOR(1024)
LIMIT 40;
"@
            $ex2 = Invoke-Crdb -Url $BaseUrl -Statements @($explainSql2) -Format 'table'
            Write-Probe -Text $ex2.Text -Path $ClusterProbe
            $namesIndex2 = ($ex2.Text -match [regex]::Escape($selectedIndex))
            $fullScan2   = ($ex2.Text -match '(?i)FULL SCAN')
            if ($ex2.Ok -and $namesIndex2 -and -not $fullScan2) {
                $indexChosen = $true
                $explainText = $ex2.Text
                Write-Probe -Text "The index IS chosen when the query vector is a literal. provenance_db.repositories.evidence.ann_search() binds `$3 :: VECTOR(1024) as a parameter (DDL S5.5), which is the literal-equivalent shape, so this counts as PASS. Record this distinction." -Path $ClusterProbe -Color 'Yellow'
            }
        }
        Write-Result -Label 'PB-2 index-chosen' -Pass $indexChosen -Detail ("index {0}, operator {1}" -f $selectedIndex, $distOp) -Path $ClusterProbe
    } else {
        Write-Probe -Text 'No variant was created, so there is no index to prove chosen.' -Path $ClusterProbe -Color 'Red'
    }

    $R.PB2.Variant = $selectedVariant
    $R.PB2.Chosen  = $indexChosen
    $R.PB2.Pass    = ([bool]$selectedVariant -and $indexChosen)
    if ($R.PB2.Pass) {
        $R.PB2.Note = "VARIANT: $selectedVariant, index chosen by the planner"
    } elseif ($selectedVariant) {
        $R.PB2.Note = "VARIANT: $selectedVariant created but NOT chosen (full scan) -> G6.2 blocked"
    } else {
        $R.PB2.Note = 'no vector index variant is accepted by this cluster'
    }

    if ($selectedVariant -eq 'C') {
        Write-Probe -Text "`nVARIANT C CONSEQUENCE (binding): set EMBEDDING_NORMALIZATION=L2_UNIT and request Titan v2 embeddings with `"normalize`": true. On unit vectors l2(a,b)^2 = 2 - 2*cos(a,b), so L2 ordering IS cosine ordering and retrieval ranking is unchanged. The frozen embedding contract is preserved: amazon.titan-embed-text-v2:0, 1024 dimensions, embedding version v1." -Path $ClusterProbe -Color 'Yellow'
    }
}

# ---- PB-1 final verdict, now that PB-2 is known --------------------------------
if ($SqlReady) {
    Write-Banner -Title 'PB-1 FINAL VERDICT' -Path $ClusterProbe
    if ($pb1State -eq 'ENABLED_BY_PROBE' -or $pb1State -eq 'ALREADY_ENABLED') {
        $R.PB1.Pass = $true
        $R.PB1.Note = "feature.vector_index.enabled = true ($pb1State)"
    } elseif ($R.PB2.Pass -or [bool]$selectedVariant) {
        $R.PB1.Pass = $true
        $R.PB1.Note = "setting not settable/present ($pb1State) but CREATE VECTOR INDEX succeeded -- there is no gate to open"
    } else {
        $R.PB1.Pass = $false
        $R.PB1.Note = "$pb1State and PB-2 also failed"
    }
    Write-Result -Label 'PB-1' -Pass $R.PB1.Pass -Detail $R.PB1.Note -Path $ClusterProbe

    if (-not $R.PB1.Pass) {
        $ladder = @"

#########################################################################
#  PB-1 FAILED. THREE-STEP FALLBACK LADDER. TAKE THEM IN THIS ORDER.    #
#  Do NOT escalate privileges and do NOT migrate to a paid tier first.  #
#########################################################################

  STEP 1  Re-run PB-2 on its own.
          On several builds the setting is absent because the feature is
          unconditional. A successful CREATE VECTOR INDEX makes PB-1 moot.
          Command:  .\ops\probes\phase0-probe.ps1 -SkipAws

  STEP 2  If PB-2 also fails, open a CockroachDB Cloud support request to
          enable vector indexing on cluster $ClusterId (BASIC,
          AWS us-east-1) and record the ticket id in ops/cluster-probe.txt.
          Continue Phase 1 and Phase 2 work meanwhile: nothing before
          Phase 6 needs the index.

  STEP 3  If it cannot be enabled, take the frozen fallback from
          CANONICAL_DECISIONS.md: "L2-normalized vector index; if no vector
          index works, disclose brute-force user-partition scan and fail the
          sponsor vector-index submission gate."
            - set PV_RETRIEVAL_MODE=BRUTE_FORCE_PARTITION
            - it scans within user_id = `$1 over ~16,035 hero-partition rows
            - it is survivable for a demo and it is NOT vector indexing
            - it MUST be disclosed in Judge Mode and in README.md
            - G6.2 and submission item S5 tool 1 stay BLOCKED
"@
        Write-Probe -Text $ladder -Path $ClusterProbe -Color 'Red'
    }
}

# =============================================================================
# 8. PB-3  --  Generated stored column, row-level TTL, column families
#              Consumes P9, P10, P11.
# =============================================================================

if ($SqlReady) {
    Write-Banner -Title 'PB-3  Generated STORED column, row TTL, column families  (P9..P11)' -Path $ClusterProbe

    Write-Probe -Text "`n-- P9. Row-level TTL, used by idempotency_records." -Path $ClusterProbe -Color 'White'
$p9Sql = @'
CREATE TABLE IF NOT EXISTS _pv_probe3 (
    id UUID NOT NULL PRIMARY KEY,
    expires_at TIMESTAMPTZ NOT NULL
) WITH (ttl_expiration_expression = 'expires_at', ttl_job_cron = '@hourly');
'@
    $p9 = Invoke-Crdb -Url $BaseUrl -Statements @($p9Sql)
    Write-Probe -Text $p9.Text -Path $ClusterProbe
    $R.PB3.Ttl = $p9.Ok
    Write-Result -Label 'P9' -Pass $p9.Ok -Detail 'row-level TTL clause' -Path $ClusterProbe
    if (-not $p9.Ok) {
        Write-Probe -Text "FALLBACK for P9: drop the WITH (...) clause from idempotency_records and add a scheduled DELETE ... WHERE expires_at < now() worker." -Path $ClusterProbe -Color 'Yellow'
    }

    Write-Probe -Text "`n-- P10. Confirm STORED computed columns, used by evidence_items.is_retrieval_eligible." -Path $ClusterProbe -Color 'White'
$p10Create = @'
CREATE TABLE IF NOT EXISTS _pv_probe4 (
    id UUID NOT NULL PRIMARY KEY,
    s  STRING NOT NULL,
    b  BOOL NOT NULL AS (s = 'ACTIVE') STORED
);
'@
    $p10a = Invoke-Crdb -Url $BaseUrl -Statements @($p10Create)
    Write-Probe -Text $p10a.Text -Path $ClusterProbe
    $storedOk = $false
    if ($p10a.Ok) {
        $p10b = Invoke-Crdb -Url $BaseUrl -Statements @(
            "INSERT INTO _pv_probe4 (id, s) VALUES (gen_random_uuid(), 'ACTIVE'), (gen_random_uuid(), 'RETRACTED');",
            'SELECT s, b FROM _pv_probe4 ORDER BY s;'
        )
        Write-Probe -Text $p10b.Text -Path $ClusterProbe
        $storedOk = ($p10b.Ok -and $p10b.Text -match '(?m)^ACTIVE,(true|t)$' -and $p10b.Text -match '(?m)^RETRACTED,(false|f)$')
    }
    $R.PB3.Stored = $storedOk
    Write-Result -Label 'P10' -Pass $storedOk -Detail 'ACTIVE->true and RETRACTED->false, computed by the database' -Path $ClusterProbe
    if ($storedOk) {
        Write-Probe -Text "DECISION for the migration author: use the generated column -- is_retrieval_eligible BOOL NOT NULL AS (retraction_status = 'ACTIVE') STORED" -Path $ClusterProbe -Color 'Green'
    } else {
        Write-Probe -Text @"
FALLBACK for P10 (CANONICAL_DECISIONS.md: "Plain boolean plus consistency check, written only by the kernel"):
    is_retrieval_eligible BOOL NOT NULL DEFAULT true,
    CONSTRAINT ck_evidence_retrieval_flag_consistent CHECK (
        is_retrieval_eligible = (retraction_status = 'ACTIVE')
    )
The Kernel's retraction statement (DDL S5.6) then sets both columns in the same UPDATE. Nothing else may write either one.
"@ -Path $ClusterProbe -Color 'Yellow'
    }

    Write-Probe -Text "`n-- P11. Column families alongside a VECTOR column." -Path $ClusterProbe -Color 'White'
$p11Sql = @'
CREATE TABLE IF NOT EXISTS _pv_probe5 (
    id UUID NOT NULL PRIMARY KEY,
    t  STRING NOT NULL,
    v  VECTOR(1024),
    FAMILY f_meta (id),
    FAMILY f_text (t),
    FAMILY f_vec  (v)
);
'@
    $p11 = Invoke-Crdb -Url $BaseUrl -Statements @($p11Sql)
    Write-Probe -Text $p11.Text -Path $ClusterProbe
    $R.PB3.Families = $p11.Ok
    Write-Result -Label 'P11' -Pass $p11.Ok -Detail 'FAMILY clauses coexist with a VECTOR column' -Path $ClusterProbe
    if (-not $p11.Ok) {
        Write-Probe -Text "FALLBACK for P11: delete the three FAMILY lines from evidence_items. Storage optimisation only, not a semantic requirement." -Path $ClusterProbe -Color 'Yellow'
    }

    # PB-3's own question is P10. P9 and P11 ride along because their fallbacks are
    # cheap and their failure is silent.
    $R.PB3.Pass = $storedOk
    $R.PB3.Note = if ($storedOk) { 'generated STORED column supported' } else { 'STORED computed column unavailable -> plain flag + CHECK constraint' }
    Write-Result -Label 'PB-3' -Pass $R.PB3.Pass -Detail $R.PB3.Note -Path $ClusterProbe

    # ---- cleanup: headed "-- CLEANUP", never "-- P12" (G0.6 counts "^-- P") -----
    Write-Probe -Text "`n-- CLEANUP" -Path $ClusterProbe -Color 'White'
    if ($KeepProbeObjects) {
        Write-Probe -Text 'skipped: -KeepProbeObjects was supplied. Drop _pv_probe, _pv_probe2, _pv_probe3, _pv_probe4, _pv_probe5 before running migration 0001.' -Path $ClusterProbe -Color 'Yellow'
    } else {
        $cl = Invoke-Crdb -Url $BaseUrl -Statements @('DROP TABLE IF EXISTS _pv_probe, _pv_probe2, _pv_probe3, _pv_probe4, _pv_probe5 CASCADE;')
        Write-Probe -Text $cl.Text -Path $ClusterProbe
        Write-Probe -Text ("probe tables dropped: {0}" -f $cl.Ok) -Path $ClusterProbe
    }
}

# =============================================================================
# 9. PB-4  --  pv_agent_reader reads views and is denied base tables
#              PHASE-STOPPING on failure (stops Phase 11). Own transcript.
# =============================================================================

$grantHeader = @"
== Provenance Phase 0 probe PB-4 -- view read / base-table deny ==
run_at    : $RunStamp
question  : does a role granted SELECT on a view alone read the view (executing with the
            owner's table privileges) while being refused every base table?
authority : docs/ops/41_RUNBOOK.md S3.4; re-asserted at G11.1 / G11.2 against agent_*_v1
note      : Phase 0 runs a two-object miniature. The five agent_*_v1 views
            (agent_case_context_v1, agent_active_beliefs_v1, agent_belief_lineage_v1,
            agent_evidence_retrieval_v1, agent_open_obligations_v1) do not exist until
            migration 0008.
secrets   : the probe role's password is generated in memory, never echoed, never written.
"@
Write-Probe -Text $grantHeader -Path $GrantProbe -Color 'Gray'

if ($SqlReady) {
    Write-Banner -Title 'PB-4  view read / base-table deny  (PHASE-STOPPING)' -Path $GrantProbe

    $probePw = New-ProbePassword
    Add-SecretLiteral $probePw

    $setupOk = $true
    $steps = @(
        'CREATE TABLE IF NOT EXISTS _pv_grant_base (id UUID NOT NULL PRIMARY KEY, secret STRING NOT NULL);',
        "INSERT INTO _pv_grant_base VALUES (gen_random_uuid(), 'base-table-row');",
        'CREATE VIEW _pv_grant_v1 AS SELECT id FROM _pv_grant_base;'
    )
    foreach ($s in $steps) {
        $r = Invoke-Crdb -Url $BaseUrl -Statements @($s)
        if (-not $r.Ok) { $setupOk = $false; Write-Probe -Text $r.Text -Path $GrantProbe -Color 'Red' }
    }
    # The CREATE ROLE statement carries the password, so it is never echoed and its
    # output is scrubbed like everything else.
    $roleSql = "CREATE ROLE IF NOT EXISTS _pv_probe_reader WITH LOGIN PASSWORD '$probePw';"
    $rr = Invoke-Crdb -Url $BaseUrl -Statements @($roleSql)
    if (-not $rr.Ok) { $setupOk = $false }
    Write-Probe -Text ("create role _pv_probe_reader: exit {0}" -f $rr.ExitCode) -Path $GrantProbe
    if (-not $rr.Ok) { Write-Probe -Text $rr.Text -Path $GrantProbe -Color 'Red' }

    $grants = Invoke-Crdb -Url $BaseUrl -Statements @(
        "GRANT CONNECT ON DATABASE $AppDatabase TO _pv_probe_reader;",
        'GRANT USAGE ON SCHEMA public TO _pv_probe_reader;',
        'GRANT SELECT ON _pv_grant_v1 TO _pv_probe_reader;'
    )
    Write-Probe -Text $grants.Text -Path $GrantProbe
    Write-Probe -Text 'Deliberately granted NOTHING on _pv_grant_base.' -Path $GrantProbe -Color 'White'
    if (-not $grants.Ok) { $setupOk = $false }

    if ($setupOk) {
        $readerUrl = Join-DbUrl -Parts $UrlParts -Database $AppDatabase -User '_pv_probe_reader' -Password $probePw
        Write-Probe -Text ("connected as: {0}" -f (Get-DisplayUrl $readerUrl)) -Path $GrantProbe

        $v = Invoke-Crdb -Url $readerUrl -Statements @('SELECT count(*) FROM _pv_grant_v1;')
        Write-Probe -Text "`n[1] SELECT count(*) FROM _pv_grant_v1;" -Path $GrantProbe -Color 'White'
        Write-Probe -Text $v.Text -Path $GrantProbe

        $b = Invoke-Crdb -Url $readerUrl -Statements @('SELECT secret FROM _pv_grant_base LIMIT 1;')
        Write-Probe -Text "`n[2] SELECT secret FROM _pv_grant_base LIMIT 1;" -Path $GrantProbe -Color 'White'
        Write-Probe -Text $b.Text -Path $GrantProbe

        $i = Invoke-Crdb -Url $readerUrl -Statements @("INSERT INTO _pv_grant_base (id, secret) VALUES (gen_random_uuid(), 'x');")
        Write-Probe -Text "`n[3] INSERT INTO _pv_grant_base ...;" -Path $GrantProbe -Color 'White'
        Write-Probe -Text $i.Text -Path $GrantProbe

        # CockroachDB has shipped at least three wordings for a privilege refusal --
        #   "user X has no SELECT privilege on relation Y"
        #   "user X does not have SELECT privilege on relation Y"
        #   "permission denied: user X ..."
        # Matching only the first would report a CORRECT denial as a FAIL on the one
        # phase-stopping probe and stop Phase 11 for nothing. Match the refusal, not the phrasing.
        $denyPattern = '(?i)(no\s+{0}\s+privilege|does not have\s+{0}\s+privilege|permission denied|insufficient privilege)'
        $viewReads   = ($v.Ok -and $v.Text -match '(?m)^\s*1\s*$')
        $selDenied   = ((-not $b.Ok) -and $b.Text -match ($denyPattern -f 'SELECT'))
        $insDenied   = ((-not $i.Ok) -and $i.Text -match ($denyPattern -f 'INSERT'))
        $R.PB4.Pass  = ($viewReads -and $selDenied -and $insDenied)

        Write-Probe -Text ("`nview_read_succeeds={0}  base_select_denied={1}  base_insert_denied={2}" -f $viewReads, $selDenied, $insDenied) -Path $GrantProbe -Color 'White'
        Write-Probe -Text 'The view read must succeed AND both base-table statements must be refused. One without the other is a failure.' -Path $GrantProbe -Color 'Gray'

        if ($R.PB4.Pass) {
            $R.PB4.Note = 'view executes with owner privileges; base table denied for SELECT and INSERT'
        } elseif (-not $viewReads -and $b.Text -match '_pv_grant_base') {
            $R.PB4.Note = 'views do NOT execute with owner privileges here; the agent-safe view design does not hold as written'
        } elseif ($b.Ok) {
            $R.PB4.Note = 'the role has base-table reach it must not have; inspect SHOW GRANTS and every ALTER DEFAULT PRIVILEGES in effect'
        } else {
            $R.PB4.Note = 'unexpected shape; read the three statement outputs above'
        }
        Write-Result -Label 'PB-4' -Pass $R.PB4.Pass -Detail $R.PB4.Note -Path $GrantProbe

        if (-not $R.PB4.Pass) {
            Write-Probe -Text @"

#########################################################################
#  PB-4 FAILED. THIS IS THE ONE PHASE-STOPPING PROBE.                   #
#########################################################################

CANONICAL_DECISIONS.md: "Stop Phase 11; do not weaken grants. Use a controlled
read API until the database boundary is proven."

Concretely:
  - set PV_MCP_ENABLED=false
  - route the Interpreter's context read through the control-plane retrieval
    endpoint, which G11.7 requires to stay functional regardless
  - record here that the CockroachDB Cloud Managed MCP Server claim is BLOCKED

DO NOT grant pv_agent_reader base-table SELECT to make MCP work. That trades the
product's central safety claim for a demo feature, and a judge reading
information_schema.role_table_grants will find it.
"@ -Path $GrantProbe -Color 'Red'
        }

        Write-Probe -Text "`nRe-assertion command for Phase 11 (G11.1 / G11.2), against the real objects:" -Path $GrantProbe -Color 'Gray'
        Write-Probe -Text "  cockroach sql --url `"`$env:PV_DB_MIGRATOR`" --format=csv -e `"SELECT grantee, table_name, privilege_type FROM information_schema.role_table_grants WHERE grantee='pv_agent_reader' AND table_name NOT LIKE 'agent\_%\_v1';`"  ->  header only, zero data rows" -Path $GrantProbe -Color 'Gray'
    } else {
        $R.PB4.Note = 'fixture setup failed; PB-4 is inconclusive, not passing'
        Write-Result -Label 'PB-4' -Pass $false -Detail $R.PB4.Note -Path $GrantProbe
    }

    Write-Probe -Text "`n-- CLEANUP" -Path $GrantProbe -Color 'White'
    if ($KeepProbeObjects) {
        Write-Probe -Text 'skipped: -KeepProbeObjects was supplied.' -Path $GrantProbe -Color 'Yellow'
    } else {
        $gc = Invoke-Crdb -Url $BaseUrl -Statements @(
            'DROP VIEW IF EXISTS _pv_grant_v1;',
            'DROP TABLE IF EXISTS _pv_grant_base CASCADE;',
            'DROP ROLE IF EXISTS _pv_probe_reader;'
        )
        Write-Probe -Text $gc.Text -Path $GrantProbe
        Write-Probe -Text ("probe view, table and role dropped: {0}" -f $gc.Ok) -Path $GrantProbe
    }
} else {
    Write-Probe -Text 'PB-4 not run: no usable SQL connection.' -Path $GrantProbe -Color 'Red'
    $R.PB4.Note = 'not run (no SQL connection)'
}

# =============================================================================
# 10. PB-5  --  Bedrock model access. Three model ids, two real Messages calls,
#               one embedding call asserting 1024 dims and L2 norm near 1.0.
# =============================================================================

$bedrockHeader = @"
== Provenance Phase 0 probe PB-5 -- Bedrock model access ==
run_at    : $RunStamp
region    : $Region
models    : us.anthropic.claude-haiku-4-5-20251001-v1:0  (Tier E, extraction and classification)
            us.anthropic.claude-opus-4-6-v1              (Tier R IN FORCE -- resolution, contradiction,
                                                          attention, drafting)
            us.anthropic.claude-opus-5                   (Tier R TARGET -- expected DENIED, D-00-004)
            amazon.titan-embed-text-v2:0                 (embeddings, 1024 dims, version v1, cosine, BARE id)
id form   : Anthropic chat models require INFERENCE PROFILE ids (us. / global. prefix).
            A bare anthropic.claude-* id is not invocable in any form on this account.
            CANONICAL_DECISIONS.md -> "Bedrock model id canon (frozen 2026-08-17)".
client    : AnthropicBedrockMantle, the Messages-API Bedrock endpoint pinned by
            docs/specs/14_PROMPTS.md S9.1. NOT the legacy AnthropicBedrock client.
sampling  : no temperature, no top_p, no top_k. All three return HTTP 400 on these models.
"@
Write-Probe -Text $bedrockHeader -Path $BedrockProbe -Color 'Gray'

if (-not $SkipAws -and $script:AwsExe) {
    Write-Banner -Title 'PB-5  Bedrock model access' -Path $BedrockProbe

    $awsVer = Invoke-Native -Exe $script:AwsExe -Arguments @('--version')
    Write-Probe -Text ("aws cli   : " + $awsVer.Text) -Path $BedrockProbe
    if ($awsVer.Text -notmatch 'aws-cli/2\.') {
        Write-Probe -Text 'AWS CLI v2 is required: v1 lacks --cli-binary-format, which the Titan embedding probe depends on.' -Path $BedrockProbe -Color 'Yellow'
    }

    $ident = Invoke-Native -Exe $script:AwsExe -Arguments @('sts', 'get-caller-identity', '--query', '[Account,Arn]', '--output', 'text')
    Write-Probe -Text ("caller    : " + $ident.Text) -Path $BedrockProbe
    if (-not $ident.Ok) {
        Write-Probe -Text 'No usable AWS credentials. Run: aws configure sso   (or aws login), then set $env:AWS_REGION = "us-east-1".' -Path $BedrockProbe -Color 'Red'
    }

    # ---- Step 1: availability -------------------------------------------------
    Write-Probe -Text "`n[Step 1] availability -- listing the three canonical model identifiers" -Path $BedrockProbe -Color 'White'
    $modelQuery = "modelSummaries[?contains(modelId,'claude-opus-5')||contains(modelId,'claude-haiku-4-5')||contains(modelId,'titan-embed-text-v2')].[modelId,modelLifecycle.status]"
    $listTable = Invoke-Native -Exe $script:AwsExe -Arguments @(
        'bedrock', 'list-foundation-models', '--region', $Region, '--query', $modelQuery, '--output', 'table')
    Write-Probe -Text $listTable.Text -Path $BedrockProbe

    $listJson = Invoke-Native -Exe $script:AwsExe -Arguments @(
        'bedrock', 'list-foundation-models', '--region', $Region, '--query', $modelQuery, '--output', 'json')
    $listedIds = @()
    if ($listJson.Ok) {
        try {
            $parsed = $listJson.Text | ConvertFrom-Json
            foreach ($row in @($parsed)) { if ($row -and $row.Count -ge 1) { $listedIds += [string]$row[0] } }
        } catch { }
    }
    $haikuListed = @($listedIds -match 'claude-haiku-4-5').Count -gt 0
    $opusListed  = @($listedIds -match 'claude-opus-5').Count  -gt 0
    $titanListed = @($listedIds -match 'titan-embed-text-v2').Count -gt 0
    Write-Probe -Text ("listed: haiku-4-5={0}  opus-5={1}  titan-embed-text-v2={2}" -f $haikuListed, $opusListed, $titanListed) -Path $BedrockProbe -Color 'White'
    Write-Probe -Text 'Listing a model proves it exists in the region. It does NOT prove you have access. Steps 2 and 3 do.' -Path $BedrockProbe -Color 'Gray'

    # ---- Step 2: one real Tier E call and one real Tier R call ---------------
    Write-Probe -Text "`n[Step 2] one real Tier E call and one real Tier R call, through AnthropicBedrockMantle" -Path $BedrockProbe -Color 'White'
    $tierEOk = $false
    $tierROk = $false
    $tierRTargetOk = $false
    if (-not $script:PythonPath) {
        Write-Probe -Text "BLOCKED: '$PythonExe' is not on PATH. Install Python 3.12.x and re-run. This is a tooling gap, not a Bedrock access failure -- do not record it as one." -Path $BedrockProbe -Color 'Yellow'
    } else {
        $sdkCheck = Invoke-Native -Exe $script:PythonPath -Arguments @('-c', 'import anthropic,sys; sys.stdout.write(anthropic.__version__)')
        if (-not $sdkCheck.Ok) {
            Write-Probe -Text "BLOCKED: the anthropic SDK is not importable. Install it with:  $PythonExe -m pip install --upgrade anthropic boto3" -Path $BedrockProbe -Color 'Yellow'
            Write-Probe -Text $sdkCheck.Text -Path $BedrockProbe
        } else {
            Write-Probe -Text ("anthropic sdk version: " + $sdkCheck.Text) -Path $BedrockProbe
            $pyPath = Join-Path $TempDir 'pb5_messages.py'
            $pySrc = @"
import sys, traceback
REGION = "$Region"
try:
    from anthropic import AnthropicBedrockMantle
except Exception:
    print("BLOCKED: AnthropicBedrockMantle is not exported by the installed anthropic SDK.")
    print("         Upgrade with: python -m pip install --upgrade anthropic")
    print("         Do NOT substitute the legacy AnthropicBedrock client: it targets")
    print("         bedrock-runtime InvokeModel with a different request shape.")
    sys.exit(3)

client = AnthropicBedrockMantle(aws_region=REGION)
rc = 0
# Inference-profile ids, not bare ids. CANONICAL_DECISIONS.md -> "Bedrock model
# id canon (frozen 2026-08-17)": a bare `anthropic.claude-*` chat id is not
# invocable in any form on this account, and every bare id elsewhere in the pack
# is superseded. Tier R runs on 4.6 because `us.anthropic.claude-opus-5` is
# ACCESS DENIED to this account (D-00-004); it is probed third, separately, so
# the transcript records the day the grant lands. (D-00-005.)
for tier, model_id in (("E", "us.anthropic.claude-haiku-4-5-20251001-v1:0"),
                       ("R", "us.anthropic.claude-opus-4-6-v1"),
                       ("R-TARGET", "us.anthropic.claude-opus-5")):
    try:
        # No temperature, no top_p, no top_k: all three return HTTP 400 on these models.
        r = client.messages.create(
            model=model_id,
            max_tokens=16,
            messages=[{"role": "user", "content": "Reply with exactly: ok"}],
        )
        text = "".join(getattr(b, "text", "") for b in r.content).strip()
        print("tier=%s model=%s ok text=%r in=%d out=%d"
              % (tier, model_id, text, r.usage.input_tokens, r.usage.output_tokens))
    except Exception as exc:
        # R-TARGET is EXPECTED to fail until the Opus 5 grant lands (D-00-004).
        # Its failure is evidence, not a probe failure, so it does not move rc.
        if tier != "R-TARGET":
            rc = 4
        print("tier=%s model=%s FAILED %s: %s" % (tier, model_id, type(exc).__name__, exc))
        traceback.print_exc(limit=1, file=sys.stdout)
sys.exit(rc)
"@
            [System.IO.File]::WriteAllText($pyPath, $pySrc, $script:Utf8NoBom)
            $py = Invoke-Native -Exe $script:PythonPath -Arguments @($pyPath)
            Write-Probe -Text $py.Text -Path $BedrockProbe
            $tierEOk = ($py.Text -match 'tier=E model=us\.anthropic\.claude-haiku-4-5-20251001-v1:0 ok text=')
            $tierROk = ($py.Text -match 'tier=R model=us\.anthropic\.claude-opus-4-6-v1 ok text=')
            $tierRTargetOk = ($py.Text -match 'tier=R-TARGET model=us\.anthropic\.claude-opus-5 ok text=')
        }
    }
    Write-Result -Label 'PB-5 Tier E (us.anthropic.claude-haiku-4-5-20251001-v1:0)' -Pass $tierEOk -Detail 'one real Messages call' -Path $BedrockProbe
    Write-Result -Label 'PB-5 Tier R in force (us.anthropic.claude-opus-4-6-v1)'    -Pass $tierROk -Detail 'one real Messages call' -Path $BedrockProbe
    Write-Probe -Text ("PB-5 Tier R TARGET (us.anthropic.claude-opus-5): " +
        $(if ($tierRTargetOk) { 'GRANTED -- close D-00-004 and move BEDROCK_REASONING_MODEL_ID onto it' }
          else { 'STILL DENIED -- D-00-004 stays OPEN; 4.6 remains in force and must be disclosed' })) -Path $BedrockProbe -Color 'Yellow'

    # ---- Step 3: embeddings, dimension and L2 norm asserted ------------------
    Write-Probe -Text "`n[Step 3] embeddings -- amazon.titan-embed-text-v2:0, dimensions and L2 norm asserted" -Path $BedrockProbe -Color 'White'
    $embOk = $false
    $bodyPath = Join-Path $TempDir 'titan-body.json'
    $outPath  = Join-Path $TempDir 'titan-out.json'
    [System.IO.File]::WriteAllText($bodyPath, '{"inputText":"Provenance embedding probe","dimensions":1024,"normalize":true}', $script:Utf8NoBom)
    $emb = Invoke-Native -Exe $script:AwsExe -Arguments @(
        'bedrock-runtime', 'invoke-model',
        '--region', $Region,
        '--model-id', 'amazon.titan-embed-text-v2:0',
        '--cli-binary-format', 'raw-in-base64-out',
        '--content-type', 'application/json',
        '--accept', 'application/json',
        '--body', ('fileb://' + ($bodyPath -replace '\\', '/')),
        $outPath)
    if (-not $emb.Ok) {
        Write-Probe -Text $emb.Text -Path $BedrockProbe -Color 'Red'
    } elseif (Test-Path -LiteralPath $outPath) {
        try {
            $doc  = (Get-Content -LiteralPath $outPath -Raw) | ConvertFrom-Json
            $vec  = $doc.embedding
            $dims = @($vec).Count
            $sum  = 0.0
            foreach ($x in $vec) { $sum += ([double]$x * [double]$x) }
            $l2   = [math]::Sqrt($sum)
            $R.PB5.Dims = $dims
            $R.PB5.L2   = [math]::Round($l2, 7)
            Write-Probe -Text ('{{ "dims": {0}, "l2": {1} }}' -f $dims, ([math]::Round($l2, 7))) -Path $BedrockProbe
            $embOk = (($dims -eq 1024) -and ([math]::Abs($l2 - 1.0) -le 0.01))
            if ($dims -ne 1024) {
                Write-Probe -Text "dims is $dims, not 1024. Titan v2 supports several output sizes; 1024 is frozen for the life of the index. The request omitted or mis-set `"dimensions`"." -Path $BedrockProbe -Color 'Red'
            }
            if ([math]::Abs($l2 - 1.0) -gt 0.01) {
                Write-Probe -Text "l2 is $([math]::Round($l2,7)), not ~1.0. `"normalize`": true did not produce unit vectors. Variant C's L2 fallback depends on exactly this property." -Path $BedrockProbe -Color 'Red'
            }
        } catch {
            Write-Probe -Text ("could not parse the Titan response: " + $_.Exception.Message) -Path $BedrockProbe -Color 'Red'
        }
    }
    Write-Result -Label 'PB-5 embeddings (amazon.titan-embed-text-v2:0)' -Pass $embOk -Detail 'dims == 1024 and L2 norm within 0.01 of 1.0' -Path $BedrockProbe

    $R.PB5.Pass = ($tierEOk -and $tierROk -and $embOk)
    $R.PB5.Note = if ($R.PB5.Pass) {
        "all three canonical model ids invocable in $Region; embedding dims=$($R.PB5.Dims) l2=$($R.PB5.L2)"
    } else {
        "tierE=$tierEOk tierR=$tierROk embeddings=$embOk"
    }
    Write-Result -Label 'PB-5' -Pass $R.PB5.Pass -Detail $R.PB5.Note -Path $BedrockProbe

    if (-not $R.PB5.Pass) {
        Write-Probe -Text @"

PB-5 FAILURE INTERPRETATION -- these are four different faults, do not conflate them:

  AccessDeniedException "You don't have access to the model with the specified model ID"
      Model access not granted, or granted in another region. Request access in the
      Bedrock console for us-east-1 and confirm AWS_REGION. Grants are not instantaneous.

  ValidationException "The provided model identifier is invalid"
      Wrong identifier form. Use the canonical ids verbatim. G7.4 explicitly fails any
      output naming Sonnet 4.6, Gemma 4, GLM 5 or Kimi K2.5; those are stale identifiers
      from superseded drafts.

  ThrottlingException on the first call
      Account-level quota at zero. See RUNBOOK S7.5. This is NOT access denied and must
      not be reported as such.

  dims is 512 or 256
      The request omitted or mis-set "dimensions". 1024 is frozen for the life of the index.

PREDETERMINED FALLBACK (CANONICAL_DECISIONS.md): "Fixture mode for development only;
live submission remains blocked until model access works."
  - set PV_AGENT_MODE=FIXTURE
  - the real Kernel, the real database and the real event path still execute; only model
    outputs are replayed
  - a non-dismissible banner appears (G12.7) and GET /v1/version reports
    fixture_mode: true and agent_mode: "FIXTURE", which invalidates the recorded
    submission (S3)
  - fixture mode is a development unblocker and an emergency demo fallback, never a
    submission state
"@ -Path $BedrockProbe -Color 'Yellow'
    }
} else {
    $reason = if ($SkipAws) { 'skipped: -SkipAws was supplied' } else { 'the aws CLI is not on PATH' }
    Write-Probe -Text "PB-5 not run: $reason" -Path $BedrockProbe -Color 'Red'
    $R.PB5.Note = "not run ($reason)"
}

# =============================================================================
# 11. PB-6  --  Seed clone and restore path, 90-second budget
# =============================================================================

$restoreHeader = @"
== Provenance Phase 0 probe PB-6 -- seed clone and restore ==
run_at        : $RunStamp
question      : can a seeded template database be cloned fast enough to give each test
                scenario a fresh database, and does a clone survive the vector index?
budget        : restore must complete in under $RestoreBudgetSeconds seconds
authority     : docs/ops/41_RUNBOOK.md S3.6; feeds quality/20_TDD_STRATEGY.md R6

HONESTY NOTE, READ BEFORE INTERPRETING THE TIMING BELOW.
At Phase 0 the '$AppDatabase' database is empty apart from one small synthetic probe table.
This run proves the MECHANISM (BACKUP/RESTORE permitted, new_db_name accepted, vector index
survives the round trip) and produces a LOWER BOUND on the timing. It does not prove the
90-second budget at corpus scale. Re-run PB-6 after 'python -m scripts.seed --profile all'
has loaded 18,035 evidence rows (16,035 of them in the hero user's partition) and record
that second measurement before relying on per-scenario cloning.
"@
Write-Probe -Text $restoreHeader -Path $RestoreProbe -Color 'Gray'

if ($SqlReady) {
    Write-Banner -Title "PB-6  clone and restore (budget ${RestoreBudgetSeconds}s)" -Path $RestoreProbe

    Write-Probe -Text '[setup] creating a small synthetic table carrying a VECTOR column and the selected index variant' -Path $RestoreProbe -Color 'White'
    $null = Invoke-Crdb -Url $BaseUrl -Statements @(
        'DROP TABLE IF EXISTS _pv_restore_probe CASCADE;',
        'CREATE TABLE _pv_restore_probe (id UUID NOT NULL PRIMARY KEY, user_id UUID NOT NULL, embedding VECTOR(1024));'
    )
    $seedSql = @"
INSERT INTO _pv_restore_probe (id, user_id, embedding)
SELECT gen_random_uuid(),
       '00000000-0000-4000-8000-000000000001',
       ('[' || (SELECT string_agg((random() + g.i)::STRING, ',')
                FROM generate_series(1, 1024)) || ']')::VECTOR(1024)
FROM generate_series(1, 200) AS g(i);
"@
    $sd = Invoke-Crdb -Url $BaseUrl -Statements @($seedSql)
    Write-Probe -Text ("seeded 200 rows: exit {0}" -f $sd.ExitCode) -Path $RestoreProbe
    if (-not $sd.Ok) { Write-Probe -Text $sd.Text -Path $RestoreProbe -Color 'Red' }

    $idxCreated = $false
    if ($selectedVariant) {
        $idxSql = switch ($selectedVariant) {
            'A' { 'CREATE VECTOR INDEX _pv_restore_ann_idx ON _pv_restore_probe (user_id, embedding vector_cosine_ops);' }
            'B' { 'CREATE INDEX _pv_restore_ann_idx ON _pv_restore_probe USING cspann (user_id, embedding vector_cosine_ops);' }
            'C' { 'CREATE VECTOR INDEX _pv_restore_ann_idx ON _pv_restore_probe (user_id, embedding);' }
        }
        $ix = Invoke-Crdb -Url $BaseUrl -Statements @($idxSql)
        $idxCreated = $ix.Ok
        Write-Probe -Text ("created vector index using variant {0}: {1}" -f $selectedVariant, $idxCreated) -Path $RestoreProbe
        if (-not $ix.Ok) { Write-Probe -Text $ix.Text -Path $RestoreProbe -Color 'Red' }
    } else {
        Write-Probe -Text 'No vector index variant was selected by PB-2, so the "does the index survive a restore" half of PB-6 cannot be answered. The BACKUP/RESTORE mechanism is still tested.' -Path $RestoreProbe -Color 'Yellow'
    }

    Write-Probe -Text "`n== template backup ==" -Path $RestoreProbe -Color 'White'
$backupSql = @'
BACKUP DATABASE provenance INTO 'userfile://defaultdb.public.userfiles_$user/pv-template';
'@
    $bk = Invoke-CrdbTimed -Url $BaseUrl -Statements @($backupSql)
    Write-Probe -Text $bk.Text -Path $RestoreProbe
    Write-Probe -Text ("backup elapsed: {0}s (exit {1})" -f $bk.Seconds, $bk.ExitCode) -Path $RestoreProbe -Color 'White'

    $restoreOk = $false
    $indexSurvived = $false
    if ($bk.Ok) {
        $null = Invoke-Crdb -Url $BaseUrl -Statements @("DROP DATABASE IF EXISTS $CloneDatabase CASCADE;")
        Write-Probe -Text "`n== restore as a new logical database ==" -Path $RestoreProbe -Color 'White'
$restoreSqlTemplate = @'
RESTORE DATABASE provenance FROM LATEST IN 'userfile://defaultdb.public.userfiles_$user/pv-template' WITH new_db_name = '__CLONE__';
'@
        $restoreSql = $restoreSqlTemplate.Replace('__CLONE__', $CloneDatabase)
        $rs = Invoke-CrdbTimed -Url $BaseUrl -Statements @($restoreSql)
        Write-Probe -Text $rs.Text -Path $RestoreProbe
        Write-Probe -Text ("restore elapsed: {0}s (budget {1}s, exit {2})" -f $rs.Seconds, $RestoreBudgetSeconds, $rs.ExitCode) -Path $RestoreProbe -Color 'White'
        $R.PB6.Seconds = $rs.Seconds
        $restoreOk = ($rs.Ok -and $rs.Seconds -lt $RestoreBudgetSeconds)

        if ($rs.Ok) {
            Write-Probe -Text "`n== the clone kept its index and its rows ==" -Path $RestoreProbe -Color 'White'
            $vf = Invoke-Crdb -Url (Join-DbUrl -Parts $UrlParts -Database $CloneDatabase) -Statements @(
                'SELECT count(*) AS probe_rows FROM _pv_restore_probe;'
            )
            Write-Probe -Text $vf.Text -Path $RestoreProbe
            $vi = Invoke-Crdb -Url (Join-DbUrl -Parts $UrlParts -Database $CloneDatabase) -Statements @(
                'SHOW INDEXES FROM _pv_restore_probe;'
            ) -Format 'table'
            Write-Probe -Text $vi.Text -Path $RestoreProbe
            $rowsKept      = ($vf.Ok -and $vf.Text -match '(?m)^\s*200\s*$')
            $indexSurvived = ($idxCreated -and $vi.Ok -and $vi.Text -match '_pv_restore_ann_idx' -and $vi.Text -match 'user_id')
            Write-Probe -Text ("rows_kept=200: {0}   vector_index_survived: {1}" -f $rowsKept, $indexSurvived) -Path $RestoreProbe -Color 'White'
            if ($idxCreated -and -not $indexSurvived) {
                Write-Probe -Text 'The index did not survive the restore. Mitigation from RUNBOOK S3.6: restore, then CREATE VECTOR INDEX from the recorded variant, then re-time. If that stays under budget the clone path is still viable.' -Path $RestoreProbe -Color 'Yellow'
            }
            $restoreOk = ($restoreOk -and $rowsKept)
        }
    } else {
        Write-Probe -Text 'BACKUP failed. On managed BASIC clusters userfile BACKUP is a plausible restriction. Read the error above verbatim.' -Path $RestoreProbe -Color 'Red'
    }

    $R.PB6.Pass = ($restoreOk -and ((-not $idxCreated) -or $indexSurvived))
    $R.PB6.Note = if ($R.PB6.Pass) {
        "clone path viable at Phase 0 scale (restore $($R.PB6.Seconds)s); MUST be re-timed after --profile all"
    } elseif (-not $bk.Ok) {
        'BACKUP to userfile is not permitted on this cluster'
    } elseif (-not $restoreOk) {
        "restore failed or exceeded the ${RestoreBudgetSeconds}s budget"
    } else {
        'restore succeeded but the vector index did not survive'
    }
    Write-Result -Label 'PB-6' -Pass $R.PB6.Pass -Detail $R.PB6.Note -Path $RestoreProbe

    if (-not $R.PB6.Pass) {
        Write-Probe -Text @"

PREDETERMINED FALLBACK (CANONICAL_DECISIONS.md): "Sequential scenarios with transaction
rollback; isolate live-model writes explicitly." Plus quality/20_TDD_STRATEGY.md R6: the
commit lane uses a hero-lite profile with 500 decoys, and the full 18,000-row corpus stays
in the nightly retrieval lane.

Isolation ALWAYS retains the cross-tenant honeypot rows. iso-a and iso-b are never dropped
to save time, because they are the only thing that makes G6.3(a) non-vacuous.

Cheapest working fallback if BACKUP/RESTORE is unavailable at all:
    cockroach sql --url "`$env:PV_DB_MIGRATOR" -e "CREATE DATABASE provenance_scn_01;"
    `$env:PV_TARGET_DB = 'provenance_scn_01'; alembic upgrade head
    `$env:PV_TARGET_DB = 'provenance_scn_01'; python -m scripts.seed --profile hero   # ~20 s, no decoys
"@ -Path $RestoreProbe -Color 'Yellow'
    }

    Write-Probe -Text "`n-- CLEANUP" -Path $RestoreProbe -Color 'White'
    if ($KeepProbeObjects) {
        Write-Probe -Text "skipped: -KeepProbeObjects was supplied. Drop $CloneDatabase and provenance._pv_restore_probe before running migration 0001." -Path $RestoreProbe -Color 'Yellow'
    } else {
        $rc1 = Invoke-Crdb -Url $BaseUrl -Statements @("DROP DATABASE IF EXISTS $CloneDatabase CASCADE;", 'DROP TABLE IF EXISTS _pv_restore_probe CASCADE;')
        Write-Probe -Text $rc1.Text -Path $RestoreProbe
        Write-Probe -Text ("clone database and probe table dropped: {0}" -f $rc1.Ok) -Path $RestoreProbe
        if ($script:CockroachExe) {
            $uf = Invoke-Native -Exe $script:CockroachExe -Arguments @('userfile', 'delete', '--url', $BaseUrl, 'pv-template')
            Write-Probe -Text ("userfile delete pv-template: exit {0}" -f $uf.ExitCode) -Path $RestoreProbe
            if (-not $uf.Ok) { Write-Probe -Text $uf.Text -Path $RestoreProbe -Color 'Yellow' }
        }
    }
} else {
    Write-Probe -Text 'PB-6 not run: no usable SQL connection.' -Path $RestoreProbe -Color 'Red'
    $R.PB6.Note = 'not run (no SQL connection)'
}

# =============================================================================
# 12. Decision record: ops/decisions/VECTOR_INDEX_VARIANT.md
#     G0.6 greps for exactly one line matching ^VARIANT: (A|B|C)$
# =============================================================================

if ($selectedVariant) {
    $variantBody = @"
# Vector index variant decision

Decided by the Phase 0 probe PB-2 on $RunStamp against cluster ``$ClusterId`` (BASIC, AWS us-east-1).
Authority for the three variants: ``docs/specs/10_DATABASE_DDL.md`` S5.1 / S5.2 / S5.3.

VARIANT: $selectedVariant

## What that means for the migration

| Item | Value |
|---|---|
| Canonical index name | ``evidence_embedding_ann_idx`` |
| Statement to use | ``docs/specs/10_DATABASE_DDL.md`` S5.$(switch ($selectedVariant) { 'A' {'1'} 'B' {'2'} 'C' {'3'} }) |
| Prefix column | ``user_id`` first, mandatory. It is the mechanism by which ANN physically cannot return another user's evidence, not a performance hint. |
| Distance operator in ``ann_search()`` | $(if ($selectedVariant -eq 'C') { '``<->`` (L2), which on unit vectors gives exactly cosine ordering' } else { '``<=>`` (cosine)' }) |
| ``EMBEDDING_NORMALIZATION`` | $(if ($selectedVariant -eq 'C') { '``L2_UNIT`` -- REQUIRED, and Titan v2 must be called with `"normalize": true`' } else { '``NONE``' }) |
| Optional index variant R (``evidence_embedding_ann_active_idx``) | $(if ($p8Pass) { 'permitted -- P8 passed. Build it only if retrieval evaluation shows the over-fetch is costing recall.' } else { 'NOT permitted -- P8 failed. Retraction filtering uses over-fetch-then-filter: k_raw = greatest(40, 4 * k_final), k_final = 20.' }) |
| Planner actually chose the index | **$($R.PB2.Chosen)** |

## Probe output that selected it

``````
$($variantLog -join "`n")
``````

## EXPLAIN evidence that the index is chosen

A full scan while the index exists is a failure even when the results are correct (``G6.2``).

``````
$explainText
``````
"@
    Write-TextFile -Path $VariantFile -Text $variantBody
} else {
    $variantBody = @"
# Vector index variant decision

Decided by the Phase 0 probe PB-2 on $RunStamp against cluster ``$ClusterId`` (BASIC, AWS us-east-1).

## NO VARIANT SELECTED

None of the three variants in ``docs/specs/10_DATABASE_DDL.md`` S5 was accepted by this cluster.
No ``VARIANT:`` line is written, because writing one would assert something untrue. Gate ``G0.6``
therefore fails by design until PB-1 step 2 or step 3 resolves.

Frozen fallback in force (``CANONICAL_DECISIONS.md``): brute-force user-partition scan.

- ``PV_RETRIEVAL_MODE=BRUTE_FORCE_PARTITION``
- scans within ``user_id = `$1`` over roughly 16,035 hero-partition rows
- survivable for a demo, and it is **not** vector indexing
- must be disclosed in Judge Mode and in ``README.md``
- ``G6.2`` and submission item ``S5`` tool 1 stay **blocked**

## Probe output

``````
$($variantLog -join "`n")
``````
"@
    # A decision file is append-and-supersede, never overwrite-with-a-negative.
    # If a variant was chosen on some earlier run, a run that chose nothing has
    # LESS information than the file already holds -- most often because it never
    # reached the cluster at all -- and writing "NO VARIANT SELECTED" over
    # `VARIANT: A` fails G0.6 on evidence that was destroyed rather than
    # disproved. Print what it would have written and leave the file alone.
    # (D-00-005.)
    $existingVariant = $false
    if (Test-Path -LiteralPath $VariantFile) {
        $existingVariant = [bool]([regex]::IsMatch(
            [System.IO.File]::ReadAllText($VariantFile), '(?m)^VARIANT: (A|B|C)$'))
    }
    if ($existingVariant) {
        Write-Host ''
        Write-Host "REFUSING to overwrite $VariantFile." -ForegroundColor Red
        Write-Host 'It already records a selected variant, and THIS run selected none.' -ForegroundColor Yellow
        Write-Host 'A probe that reached nothing has not disproved a decision. Investigate' -ForegroundColor Yellow
        Write-Host 'why PB-2 failed on this run before touching the decision file. What this' -ForegroundColor Yellow
        Write-Host 'run would have written:' -ForegroundColor Yellow
        Write-Host ''
        Write-Host (Protect-Text $variantBody) -ForegroundColor Gray
    } else {
        Write-TextFile -Path $VariantFile -Text $variantBody
    }
}

# =============================================================================
# 13. Probe result ledger (RUNBOOK S3.7) -- pre-filled, ready to paste back
# =============================================================================

function Get-Verdict {
    param([bool] $Pass, [string] $Note)
    if ($Pass) { return 'PASS' }
    if ($Note -like 'not run*') { return 'NOT RUN' }
    return 'FAIL'
}

$pb2Cell = if ($selectedVariant) { "VARIANT: $selectedVariant" } else { 'VARIANT: none -- BRUTE_FORCE_PARTITION' }

$ledger = @"
# Provenance -- Phase 0 probe result ledger

Run: $RunStamp   Cluster: ``$ClusterId``, BASIC, AWS ``$Region``
Checkout SHA: fill in from ``git rev-parse HEAD``
Filled by: fill in

A gate reviewer reads this table first (``docs/ops/41_RUNBOOK.md`` S3.7).

| Probe | Question | Result | Variant / fallback taken | Transcript |
|---|---|---|---|---|
| PB-1 | Vector indexing enabled on Basic | $(Get-Verdict $R.PB1.Pass $R.PB1.Note) | $($R.PB1.Note) | ``ops/cluster-probe.txt`` |
| PB-2 | Prefix cosine vector index created and used | $(Get-Verdict $R.PB2.Pass $R.PB2.Note) | $pb2Cell | ``ops/cluster-probe.txt``, ``ops/decisions/VECTOR_INDEX_VARIANT.md`` |
| PB-3 | Generated ``STORED`` column | $(Get-Verdict $R.PB3.Pass $R.PB3.Note) | $($R.PB3.Note) | ``ops/cluster-probe.txt`` |
| PB-4 | View read, base-table deny | $(Get-Verdict $R.PB4.Pass $R.PB4.Note) | $($R.PB4.Note) | ``ops/grant-probe.txt`` |
| PB-5 | Bedrock access, 3 model ids | $(Get-Verdict $R.PB5.Pass $R.PB5.Note) | $($R.PB5.Note) | ``ops/bedrock-probe.txt`` |
| PB-6 | Clone and restore | $(Get-Verdict $R.PB6.Pass $R.PB6.Note) | $($R.PB6.Note) | ``ops/restore-probe.txt`` |

## Side facts recorded by the same run

| Fact | Value |
|---|---|
| P9 row-level TTL supported | $($R.PB3.Ttl) |
| P10 generated ``STORED`` column supported | $($R.PB3.Stored) |
| P11 column families beside a ``VECTOR`` column | $($R.PB3.Families) |
| P8 multi-column prefix (optional index variant R) | $p8Pass |
| Titan embedding dimensions | $($R.PB5.Dims) (must be 1024) |
| Titan embedding L2 norm | $($R.PB5.L2) (must be ~1.0) |
| PB-6 restore seconds, Phase 0 scale only | $($R.PB6.Seconds) (budget $RestoreBudgetSeconds) |

## Environment settings this run implies

``````
EMBEDDING_NORMALIZATION=$(if ($selectedVariant -eq 'C') { 'L2_UNIT' } else { 'NONE' })
PV_MCP_ENABLED=$(if ($R.PB4.Pass) { 'true' } else { 'false' })
PV_AGENT_MODE=$(if ($R.PB5.Pass) { 'LIVE' } else { 'FIXTURE' })
$(if (-not $selectedVariant) { 'PV_RETRIEVAL_MODE=BRUTE_FORCE_PARTITION' } else { '' })
``````
"@
Write-TextFile -Path $LedgerFile -Text $ledger

# =============================================================================
# 14. Console summary
# =============================================================================

Write-Host ''
Write-Host '###############################################################################' -ForegroundColor Cyan
Write-Host '#  PHASE 0 PROBE SUMMARY                                                      #' -ForegroundColor Cyan
Write-Host '###############################################################################' -ForegroundColor Cyan
Write-Host ''
foreach ($k in $R.Keys) {
    $v = $R[$k]
    $verdict = Get-Verdict $v.Pass $v.Note
    $color = switch ($verdict) { 'PASS' { 'Green' } 'FAIL' { 'Red' } default { 'Yellow' } }
    Write-Host ("  {0,-5} {1,-8} {2}" -f $k, $verdict, $v.Note) -ForegroundColor $color
}
Write-Host ''

# ---- G0.6 self-check: exactly eleven lines beginning "-- P" ---------------------
$pHeaderCount = 0
if (Test-Path -LiteralPath $ClusterProbe) {
    $clusterText  = [System.IO.File]::ReadAllText($ClusterProbe)
    $pHeaderCount = ([regex]::Matches($clusterText, '(?m)^-- P')).Count
}
$g06a = ($pHeaderCount -eq 11)
$variantLineCount = 0
if (Test-Path -LiteralPath $VariantFile) {
    $variantLineCount = ([regex]::Matches([System.IO.File]::ReadAllText($VariantFile), '(?m)^VARIANT: (A|B|C)$')).Count
}
$g06b = ($variantLineCount -eq 1)

Write-Host '  Gate G0.6 self-check' -ForegroundColor White
Write-Host ("    grep -c `"^-- P`" ops/cluster-probe.txt        -> {0}  (must be 11)  {1}" -f $pHeaderCount, $(if ($g06a) { 'OK' } else { 'MISMATCH' })) -ForegroundColor $(if ($g06a) { 'Green' } else { 'Red' })
Write-Host ("    grep -E `"^VARIANT: (A|B|C)$`" ...VARIANT.md    -> {0}  (must be 1)   {1}" -f $variantLineCount, $(if ($g06b) { 'OK' } else { 'MISMATCH' })) -ForegroundColor $(if ($g06b) { 'Green' } else { 'Red' })
Write-Host ''

Write-Host '  Transcripts written (all password-scrubbed):' -ForegroundColor White
foreach ($f in @($ClusterProbe, $GrantProbe, $BedrockProbe, $RestoreProbe, $VariantFile, $LedgerFile)) {
    $sz = if (Test-Path -LiteralPath $f) { (Get-Item -LiteralPath $f).Length } else { 0 }
    Write-Host ("    {0}  ({1} bytes)" -f $f, $sz) -ForegroundColor Gray
}
Write-Host ''
Write-Host '  Before committing, run:  gitleaks detect --source . --redact --no-banner --exit-code 1' -ForegroundColor White
Write-Host ''
Write-Host '  Ledger to paste back:' -ForegroundColor White
Write-Host ''
Write-Host ([System.IO.File]::ReadAllText($LedgerFile)) -ForegroundColor Gray

# ---- cleanup temp ---------------------------------------------------------------
try { Remove-Item -LiteralPath $TempDir -Recurse -Force -ErrorAction SilentlyContinue } catch { }

# ---- exit code ------------------------------------------------------------------
if ((-not $R.PB4.Pass) -and ($R.PB4.Note -notlike 'not run*')) {
    Write-Host 'EXIT 2 -- PB-4 FAILED. This is the one phase-stopping probe (it stops Phase 11).' -ForegroundColor Red
    exit 2
}
$allPass = $R.PB1.Pass -and $R.PB2.Pass -and $R.PB3.Pass -and $R.PB4.Pass -and $R.PB5.Pass -and $R.PB6.Pass
if ($allPass) {
    Write-Host 'EXIT 0 -- all six probes passed.' -ForegroundColor Green
    exit 0
}
Write-Host 'EXIT 1 -- at least one probe failed or did not run. Every failure above has a frozen fallback; take it, do not redesign.' -ForegroundColor Yellow
exit 1
