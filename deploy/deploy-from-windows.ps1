param([string]$ServerIp, [string]$KeyPath, [int]$Port = 22, [switch]$Diagnose, [switch]$Repair)
$ErrorActionPreference = 'Stop'
function Invoke-Checked([string]$Program, [string[]]$Arguments) {
    & $Program @Arguments
    if ($LASTEXITCODE -ne 0) { throw "Command failed: $Program (exit $LASTEXITCODE). See the message above." }
}
$projectRoot = Split-Path -Parent $PSScriptRoot
$temporary = $null
try {
    if ($Diagnose -and $Repair) { throw 'Choose diagnostics or repair, not both.' }
    foreach ($program in @('ssh.exe','scp.exe','py.exe')) {
        if (-not (Get-Command $program -ErrorAction SilentlyContinue)) { throw "$program not found. Windows OpenSSH Client and Python 3.12 are required." }
    }
    if (-not $ServerIp) { $ServerIp = Read-Host 'Public IPv4 address of your server' }
    $parsedIp = $null
    if (-not [System.Net.IPAddress]::TryParse($ServerIp,[ref]$parsedIp) -or $parsedIp.AddressFamily -ne [System.Net.Sockets.AddressFamily]::InterNetwork) { throw 'Enter a valid IPv4 address.' }
    $ServerIp = $parsedIp.ToString()
    if ($Port -lt 1 -or $Port -gt 65535) { throw 'Invalid SSH port.' }
    if (-not $KeyPath) {
        Add-Type -AssemblyName System.Windows.Forms
        $picker = New-Object System.Windows.Forms.OpenFileDialog
        $picker.Title = 'Select your SSH key (for example 1.ppk)'
        $picker.Filter = 'SSH keys (*.ppk;*.pem)|*.ppk;*.pem|All files (*.*)|*.*'
        if ($picker.ShowDialog() -ne [System.Windows.Forms.DialogResult]::OK) { throw 'Key selection cancelled.' }
        $KeyPath = $picker.FileName
    }
    $KeyPath = (Resolve-Path -LiteralPath $KeyPath).Path
    $temporary = Join-Path ([System.IO.Path]::GetTempPath()) ('egrcrm-deploy-' + [guid]::NewGuid().ToString('N'))
    New-Item -ItemType Directory -Path $temporary | Out-Null
    $identity = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
    Invoke-Checked 'icacls.exe' @($temporary,'/inheritance:r','/grant:r',"${identity}:(OI)(CI)F")
    $privateKey = Join-Path $temporary 'server_key'
    Invoke-Checked 'py.exe' @('-3.12',(Join-Path $PSScriptRoot 'key_to_openssh.py'),$KeyPath,$privateKey)
    $remote = "root@$ServerIp"
    $sshArgs = @('-i',$privateKey,'-p',"$Port",'-o','BatchMode=yes','-o','StrictHostKeyChecking=accept-new','-o','ConnectTimeout=15','-o','ServerAliveInterval=30','-o','ServerAliveCountMax=4')
    $scpArgs = @('-i',$privateKey,'-P',"$Port",'-o','BatchMode=yes','-o','StrictHostKeyChecking=accept-new','-o','ConnectTimeout=15','-o','ServerAliveInterval=30','-o','ServerAliveCountMax=4')
    if ($Diagnose) {
        $remoteScript = '/root/egrcrm-diagnostic-' + [guid]::NewGuid().ToString('N') + '.sh'
        $report = Join-Path $projectRoot ('server-diagnostic-' + (Get-Date -Format 'yyyyMMdd-HHmmss') + '.txt')
        Invoke-Checked 'scp.exe' ($scpArgs + @((Join-Path $PSScriptRoot 'lite\diagnose.sh'),"${remote}:$remoteScript"))
        & ssh.exe @sshArgs $remote "trap 'rm -f $remoteScript' EXIT; bash $remoteScript 2>&1" | Tee-Object -Variable diagnosticLines
        $diagnosticExit = $LASTEXITCODE
        $diagnosticLines | Out-File -LiteralPath $report -Encoding utf8
        if ($diagnosticExit -ne 0) { throw 'Could not collect server diagnostics.' }
        Write-Host "Diagnostic report saved: $report"
        return
    }
    $upload = $null
    if (-not $Repair) {
        Write-Host 'Checking whether the existing CRM needs the directory-access repair...'
        & ssh.exe @sshArgs $remote 'if test -f /etc/egrcrm/egrcrm.env && test -L /opt/egrcrm/current && id egrcrm >/dev/null 2>&1 && ! runuser -u egrcrm -- test -x /opt/egrcrm; then exit 0; else exit 1; fi'
        if ($LASTEXITCODE -eq 0) { $Repair = $true }
        elseif ($LASTEXITCODE -ne 1) { throw 'Could not inspect the server over SSH.' }
    }
    if ($Repair) {
        Write-Host 'Repairing the existing installation without reinstalling packages or changing CRM data...'
        $remoteScript = '/root/egrcrm-repair-' + [guid]::NewGuid().ToString('N') + '.sh'
        Invoke-Checked 'scp.exe' ($scpArgs + @((Join-Path $PSScriptRoot 'lite\repair-access.sh'),"${remote}:$remoteScript"))
        Invoke-Checked 'ssh.exe' ($sshArgs + @($remote,"trap 'rm -f $remoteScript' EXIT; bash $remoteScript $ServerIp"))
    } else {
        $archive = Join-Path $temporary 'release.tar.gz'
        $digest = & py.exe -3.12 (Join-Path $PSScriptRoot 'make_release.py') $archive --root $projectRoot
        if ($LASTEXITCODE -ne 0 -or $digest -notmatch '^[a-f0-9]{64}$') { throw 'Could not package the release.' }
        Write-Host 'Checking the server and authenticated SSH access...'
        Invoke-Checked 'ssh.exe' ($sshArgs + @($remote,'uname -s; free -m; df -h /'))
        $upload = '/root/egrcrm-upload-' + [guid]::NewGuid().ToString('N')
        Invoke-Checked 'ssh.exe' ($sshArgs + @($remote,"mkdir -m 700 $upload"))
        Invoke-Checked 'scp.exe' ($scpArgs + @($archive,"${remote}:$upload/release.tar.gz"))
        Invoke-Checked 'scp.exe' ($scpArgs + @((Join-Path $PSScriptRoot 'lite\install.sh'),"${remote}:$upload/install.sh"))
        Write-Host 'Installing CRM, background worker and HTTPS. This can take several minutes.'
        Invoke-Checked 'ssh.exe' ($sshArgs + @($remote,"bash $upload/install.sh $ServerIp $upload/release.tar.gz $digest"))
    }
    $ownerCode = 'from django.contrib.auth import get_user_model; import sys; sys.exit(0 if get_user_model().objects.filter(is_active=True,is_superuser=True).exists() else 10)'
    & ssh.exe @sshArgs $remote "/usr/local/sbin/egrcrm-manage shell -c '$ownerCode'"
    if ($LASTEXITCODE -eq 10) {
        Write-Host 'Create your CRM owner. Use your existing CRM username if you plan to restore its backup.'
        Write-Host 'Password characters will not be displayed.'
        Invoke-Checked 'ssh.exe' ($sshArgs + @('-t',$remote,'/usr/local/sbin/egrcrm-manage createsuperuser'))
    } elseif ($LASTEXITCODE -ne 0) { throw 'Could not check the CRM owner account.' }
    if ($upload) { Invoke-Checked 'ssh.exe' ($sshArgs + @($remote,"rm -rf $upload")) }
    Write-Host "Ready: https://$ServerIp"
    Write-Host 'Sign in, then restore your existing CRM ZIP backup through the Backups page.'
} catch {
    Write-Host $_.Exception.Message -ForegroundColor Red
    exit 1
} finally {
    if ($temporary -and (Test-Path -LiteralPath $temporary)) { Remove-Item -LiteralPath $temporary -Recurse -Force }
}
