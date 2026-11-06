#requires -Version 5.1
<#
Install unpacman by extracting the Windows ZIP and opening Install unpacman.cmd.
The same bootstrap can also be downloaded by the optional PowerShell command.
WSL setup uses Microsoft's signed MSI, independently of the Store launcher.
Only Windows prerequisites are elevated. Rerun after a requested restart;
restarting requires typing RESTART, and no distribution is ever removed.
#>
[CmdletBinding()]
param(
    [string]$Distribution,
    [string]$PackageDirectory,
    [uri]$InstallerUrl = 'https://raw.githubusercontent.com/Tancredi-Schettini-Gherardini/unpacman/windows/install-unpacman-wsl.sh',
    # Local testing: a Linux path or URL understood by git inside Ubuntu.
    [string]$Repository,
    [string]$Follow,
    [switch]$NoOpen,
    [switch]$NoShortcut
)

function Invoke-UnpacmanWslCapture {
    param([string[]]$Arguments)
    if (($Arguments -join ' ') -eq '--list --verbose') {
        # A Windows machine without the WSL runtime can prompt to install it
        # even for --list. Close stdin and bound this read-only probe; setup
        # is requested explicitly below, with Windows administrator approval.
        $start = New-Object Diagnostics.ProcessStartInfo
        $start.FileName = 'wsl.exe'
        $start.Arguments = '--list --verbose'
        $start.UseShellExecute = $false
        $start.CreateNoWindow = $true
        $start.RedirectStandardInput = $true
        $start.RedirectStandardOutput = $true
        $start.RedirectStandardError = $true
        $start.StandardOutputEncoding = [Text.Encoding]::Unicode
        $start.StandardErrorEncoding = [Text.Encoding]::Unicode
        $process = New-Object Diagnostics.Process
        $process.StartInfo = $start
        try {
            $null = $process.Start()
            $process.StandardInput.Close()
            $stdout = $process.StandardOutput.ReadToEndAsync()
            $stderr = $process.StandardError.ReadToEndAsync()
            $timedOut = -not $process.WaitForExit(30000)
            if ($timedOut) {
                $process.Kill()
                $process.WaitForExit()
            }
            $text = ($stdout.Result + $stderr.Result).Replace([string][char]0, '')
            if ($timedOut) { $text = "WSL did not respond within 30 seconds. $text" }
            return [pscustomobject]@{ ExitCode = $process.ExitCode; Text = $text }
        } finally { $process.Dispose() }
    }
    # WSL listings are UTF-16; PowerShell normally decodes them. Strip any
    # remaining NULs for Windows/PowerShell combinations that do not.
    $previous = $ErrorActionPreference
    try {
        $ErrorActionPreference = 'Continue'
        $lines = @(& wsl.exe @Arguments 2>&1)
        $code = $LASTEXITCODE
    } finally { $ErrorActionPreference = $previous }
    [pscustomobject]@{ ExitCode = $code; Text = (($lines | ForEach-Object { "$_" }) -join "`n").Replace([string][char]0, '') }
}

function ConvertFrom-UnpacmanWslList {
    param([string]$Text)
    foreach ($line in $Text.Replace([string][char]0, '').Split("`n")) {
        # The state column is localized; only the name and version matter.
        if ($line -match '^\s*(\*)?\s*(.+?)\s{2,}.+?\s+([12])\s*$') {
            [pscustomobject]@{ Name = $Matches[2].Trim(); Version = [int]$Matches[3]; Default = $Matches[1] -eq '*' }
        }
    }
}

function Select-UnpacmanUbuntu {
    param([object[]]$Entries, [string]$Requested)
    if ($Requested) {
        $found = @($Entries | Where-Object Name -eq $Requested)
        if ($found.Count -ne 1) { throw "WSL distribution '$Requested' was not found. Run wsl --list --verbose to see its exact name." }
        return $found[0]
    }
    $ubuntu = @($Entries | Where-Object { $_.Name -match '^Ubuntu($|[-.])' })
    if ($ubuntu.Count -eq 0) { return $null }
    $plain = @($ubuntu | Where-Object Name -eq 'Ubuntu')
    if ($plain.Count -eq 1) { return $plain[0] }
    if ($ubuntu.Count -eq 1) { return $ubuntu[0] }
    $preferred = @($ubuntu | Where-Object Default)
    if ($preferred.Count -eq 1) { return $preferred[0] }
    throw "Several Ubuntu distributions are installed: $($ubuntu.Name -join ', '). Run this installer with -Distribution followed by the one to use."
}

function Get-UnpacmanSetupFolder {
    return (Join-Path ([Environment]::GetFolderPath('LocalApplicationData')) 'unpacman-installer')
}

function Get-UnpacmanBootTime {
    return (Get-CimInstance Win32_OperatingSystem -ErrorAction Stop).LastBootUpTime.ToUniversalTime().ToString('o')
}

function Set-UnpacmanRestartRequired {
    $folder = Get-UnpacmanSetupFolder
    $null = New-Item -ItemType Directory -Path $folder -Force
    Set-Content -LiteralPath (Join-Path $folder 'restart.txt') -Value (Get-UnpacmanBootTime)
}

function Test-UnpacmanRestartRequired {
    $marker = Join-Path (Get-UnpacmanSetupFolder) 'restart.txt'
    if (-not (Test-Path -LiteralPath $marker)) { return $false }
    if ((Get-Content -LiteralPath $marker -Raw).Trim() -eq (Get-UnpacmanBootTime)) { return $true }
    # Recent Windows builds can restart while deferring component changes.
    # Only gate on servicing when this installer has recorded a restart;
    # unrelated Windows updates must not block an otherwise working install.
    if (Test-UnpacmanServicingRestartPending) { return $true }
    Remove-Item -LiteralPath $marker -Force
    return $false
}

function Test-UnpacmanServicingRestartPending {
    return (Test-Path -LiteralPath 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Component Based Servicing\RebootPending')
}

function Get-UnpacmanRestartTypeDefinition {
    # The same Windows API used by the separately tested recovery helper.
    # Keep it inside the bootstrap so ZIP and command-line installs need no
    # extra file. Compiling this type does not request a restart.
    return @'
using System;
using System.ComponentModel;
using System.Runtime.InteropServices;

public static class UnpacmanWindowsRestart
{
    [StructLayout(LayoutKind.Sequential)]
    private struct Luid { public uint LowPart; public int HighPart; }

    [StructLayout(LayoutKind.Sequential)]
    private struct TokenPrivileges
    {
        public uint PrivilegeCount;
        public Luid Luid;
        public uint Attributes;
    }

    [DllImport("kernel32.dll")]
    private static extern IntPtr GetCurrentProcess();

    [DllImport("kernel32.dll", SetLastError = true)]
    [return: MarshalAs(UnmanagedType.Bool)]
    private static extern bool CloseHandle(IntPtr handle);

    [DllImport("advapi32.dll", SetLastError = true)]
    [return: MarshalAs(UnmanagedType.Bool)]
    private static extern bool OpenProcessToken(IntPtr process, uint access, out IntPtr token);

    [DllImport("advapi32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    [return: MarshalAs(UnmanagedType.Bool)]
    private static extern bool LookupPrivilegeValue(string system, string name, out Luid luid);

    [DllImport("advapi32.dll", SetLastError = true)]
    [return: MarshalAs(UnmanagedType.Bool)]
    private static extern bool AdjustTokenPrivileges(
        IntPtr token, bool disableAll, ref TokenPrivileges state,
        uint previousSize, out TokenPrivileges previous, out uint returnedSize);

    [DllImport("advapi32.dll", CharSet = CharSet.Unicode, ExactSpelling = true)]
    private static extern uint InitiateShutdownW(
        string machine, string message, uint gracePeriod, uint flags, uint reason);

    public static uint RequestRestartWithUpdates()
    {
        const uint TokenAdjustPrivileges = 0x20;
        const uint TokenQuery = 0x08;
        const uint PrivilegeEnabled = 0x02;
        const uint ShutdownRestart = 0x04;
        const uint ShutdownInstallUpdates = 0x40;
        const uint PlannedOsReconfiguration = 0x80020004;

        IntPtr token;
        if (!OpenProcessToken(GetCurrentProcess(), TokenAdjustPrivileges | TokenQuery, out token))
            throw new Win32Exception(Marshal.GetLastWin32Error());

        TokenPrivileges previous = new TokenPrivileges();
        bool restorePrivilege = false;
        try
        {
            Luid shutdownPrivilege;
            if (!LookupPrivilegeValue(null, "SeShutdownPrivilege", out shutdownPrivilege))
                throw new Win32Exception(Marshal.GetLastWin32Error());
            TokenPrivileges requested = new TokenPrivileges();
            requested.PrivilegeCount = 1;
            requested.Luid = shutdownPrivilege;
            requested.Attributes = PrivilegeEnabled;
            uint returnedSize;
            bool adjusted = AdjustTokenPrivileges(token, false, ref requested,
                (uint)Marshal.SizeOf(typeof(TokenPrivileges)), out previous, out returnedSize);
            int error = Marshal.GetLastWin32Error();
            if (!adjusted || error != 0)
                throw new Win32Exception(error);
            restorePrivilege = true;

            // No FORCE_SELF/FORCE_OTHERS: Windows can stop for unsaved work
            // or other signed-in users. A grace period would require force.
            return InitiateShutdownW(null, "Finish Windows setup for unpacman.",
                0, ShutdownRestart | ShutdownInstallUpdates, PlannedOsReconfiguration);
        }
        finally
        {
            if (restorePrivilege)
            {
                TokenPrivileges ignored;
                uint ignoredSize;
                AdjustTokenPrivileges(token, false, ref previous,
                    (uint)Marshal.SizeOf(typeof(TokenPrivileges)), out ignored, out ignoredSize);
            }
            CloseHandle(token);
        }
    }
}
'@
}

function Invoke-UnpacmanWindowsRestart {
    # https://learn.microsoft.com/windows/win32/api/winreg/nf-winreg-initiateshutdownw
    if (-not ('UnpacmanWindowsRestart' -as [type])) {
        Add-Type -TypeDefinition (Get-UnpacmanRestartTypeDefinition) -ErrorAction Stop
    }
    $result = [UnpacmanWindowsRestart]::RequestRestartWithUpdates()
    if ($result -ne 0) {
        $explanation = (New-Object ComponentModel.Win32Exception([int]$result)).Message
        throw "Windows declined the restart request (code $result): $explanation"
    }
}

function Write-UnpacmanRestartInstructions {
    Write-Host ''
    Write-Host 'NEXT: Windows needs to finish setup with a restart.' -ForegroundColor Yellow
    Write-Host 'This can also apply other pending Windows updates. Save your work first.'
    Write-Host 'Then sign in to the same Windows account, open this extracted folder,'
    Write-Host 'and double-click Install unpacman.cmd again. Setup will continue.'
    Write-Host 'If you started with a PowerShell command, run that same command again.'
    Write-Host 'Use the prompt below: an ordinary Restart can leave Windows changes pending.'
    try {
        $answer = Read-Host 'Type RESTART when ready, or press Enter to finish later'
    } catch {
        # A non-interactive host must never imply consent to restart.
        $answer = ''
    }
    if ($answer -cne 'RESTART') {
        Write-Host 'Restart deferred. Open this installer again when you are ready.'
        return
    }
    Invoke-UnpacmanWindowsRestart
    Write-Host 'Windows accepted the restart request. Let its configuration screens finish.'
    Write-Host 'If an application prevents the restart, save its work and reopen this installer.'
}

function Assert-UnpacmanSetupSpace {
    $drive = New-Object IO.DriveInfo($env:SystemDrive + '\')
    if ($drive.AvailableFreeSpace -lt 10GB) {
        $free = [math]::Round($drive.AvailableFreeSpace / 1GB, 1)
        throw "Only $free GB is free on $env:SystemDrive. Free at least 10 GB before setting up WSL/Ubuntu, then open this installer again."
    }
}

function Get-UnpacmanWslRelease {
    # Pinned official release; update both hashes when changing the version.
    # https://github.com/microsoft/WSL/releases/tag/3.0.3
    $architecture = $env:PROCESSOR_ARCHITECTURE
    if ($env:PROCESSOR_ARCHITEW6432) { $architecture = $env:PROCESSOR_ARCHITEW6432 }
    switch ($architecture) {
        'AMD64' { $arch = 'x64'; $sha = 'ae977286974255bd26bf925f00f40f5692c6b2084155cbc0563c09aea091f211' }
        'ARM64' { $arch = 'arm64'; $sha = '2937c7ce840bb35ec6e97f052189bdfff27f8c3463bfdd231df9a958212787a3' }
        default { throw "WSL setup requires 64-bit Windows (x64 or ARM64); this PC reports '$architecture'." }
    }
    $name = "wsl.3.0.3.0.$arch.msi"
    return [pscustomobject]@{ Name = $name; Sha256 = $sha; Url = "https://github.com/microsoft/WSL/releases/download/3.0.3/$name" }
}

function Assert-UnpacmanMicrosoftMsi {
    param([string]$Path, [string]$Sha256)
    if ((Get-FileHash -LiteralPath $Path -Algorithm SHA256 -ErrorAction Stop).Hash -ne $Sha256) {
        throw 'The WSL download does not match the Microsoft release checksum. Nothing was installed.'
    }
    $signature = Get-AuthenticodeSignature -LiteralPath $Path
    if ($signature.Status -ne 'Valid' -or $signature.SignerCertificate.Subject -notmatch '(^|,\s*)O=Microsoft Corporation(,|$)') {
        throw 'The WSL download does not have a valid Microsoft signature. Nothing was installed.'
    }
}

function Get-UnpacmanWslMsi {
    $release = Get-UnpacmanWslRelease
    $folder = Get-UnpacmanSetupFolder
    $null = New-Item -ItemType Directory -Path $folder -Force
    $path = Join-Path $folder $release.Name
    if (Test-Path -LiteralPath $path) {
        Assert-UnpacmanMicrosoftMsi $path $release.Sha256
        Write-Host 'Using the previously downloaded and verified Microsoft WSL installer.'
    } else {
        $partial = $path + '.part'
        Write-Host 'Downloading Microsoft WSL 3.0.3 (about 350 MB). This can take a few minutes.'
        try {
            $curl = Join-Path $env:WINDIR 'System32\curl.exe'
            if (Test-Path -LiteralPath $curl) {
                # Native curl displays download progress without PowerShell 5's
                # very slow Invoke-WebRequest progress rendering on large files.
                & $curl --fail --location --proto '=https' --proto-redir '=https' --connect-timeout 30 --max-time 900 --retry 2 --output $partial $release.Url | Out-Host
                if ($LASTEXITCODE -ne 0) { throw "WSL download failed (code $LASTEXITCODE). Check your connection and open this installer again." }
            } else {
                $previous = $ProgressPreference
                try {
                    $ProgressPreference = 'SilentlyContinue'
                    [Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12
                    Invoke-WebRequest -UseBasicParsing -Uri $release.Url -OutFile $partial -TimeoutSec 900 -ErrorAction Stop
                } finally { $ProgressPreference = $previous }
            }
            Assert-UnpacmanMicrosoftMsi $partial $release.Sha256
            Move-Item -LiteralPath $partial -Destination $path -Force
        } finally {
            Remove-Item -LiteralPath $partial -Force -ErrorAction SilentlyContinue
        }
    }
    return [pscustomobject]@{ Path = $path; Sha256 = $release.Sha256 }
}

function Invoke-UnpacmanRuntimeInstall {
    param([string]$Msi, [string]$Sha256, [bool]$EnableFeature, [string]$LogFolder)
    $ErrorActionPreference = 'Stop'
    $null = New-Item -ItemType Directory -Path $LogFolder -Force
    Write-Host "Windows setup logs: $LogFolder"
    $restart = $false
    if ($Msi) {
        # Recheck after elevation, before executing any downloaded installer.
        Assert-UnpacmanMicrosoftMsi $Msi $Sha256
        $log = Join-Path $LogFolder 'wsl-msi.log'
        Write-Host 'Installing Microsoft WSL. A Windows Installer progress window will open.'
        $process = Start-Process -FilePath "$env:WINDIR\System32\msiexec.exe" -ArgumentList @('/i', ('"' + $Msi + '"'), '/passive', '/norestart', '/L*v', ('"' + $log + '"')) -WindowStyle Normal -Wait -PassThru
        if ($process.ExitCode -notin @(0, 3010)) { throw "Microsoft WSL installation failed with code $($process.ExitCode). Details: $log" }
        $restart = $process.ExitCode -eq 3010
    }
    if ($EnableFeature) {
        Write-Host 'Enabling Virtual Machine Platform, which WSL 2 needs.'
        $log = Join-Path $LogFolder 'windows-feature.log'
        $process = Start-Process -FilePath "$env:WINDIR\System32\dism.exe" -ArgumentList @('/online', '/enable-feature', '/featurename:VirtualMachinePlatform', '/all', '/norestart', ('/LogPath:"' + $log + '"')) -NoNewWindow -Wait -PassThru
        if ($process.ExitCode -notin @(0, 3010)) { throw "Windows feature setup failed with code $($process.ExitCode). Details: $log" }
        $restart = $true
    }
    if ($restart) { return 3010 }
    return 0
}

function Get-UnpacmanRuntimeCommand {
    param([string]$Msi, [string]$Sha256, [bool]$EnableFeature, [string]$LogFolder)
    $data = @{Msi=$Msi;Sha256=$Sha256;EnableFeature=$EnableFeature;LogFolder=$LogFolder} | ConvertTo-Json -Compress
    $payload = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($data))
    # JSON is encoded separately so spaces, apostrophes and non-ASCII account
    # names cannot become PowerShell code in the elevated process.
    $command = "function Assert-UnpacmanMicrosoftMsi {`n${function:Assert-UnpacmanMicrosoftMsi}`n}`n"
    $command += "function Invoke-UnpacmanRuntimeInstall {`n${function:Invoke-UnpacmanRuntimeInstall}`n}`n"
    $command += "`$data = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('$payload')) | ConvertFrom-Json`n"
    return $command + @'
try {
    $result = Invoke-UnpacmanRuntimeInstall -Msi $data.Msi -Sha256 $data.Sha256 -EnableFeature $data.EnableFeature -LogFolder $data.LogFolder
    exit $result
} catch {
    Write-Host $_.Exception.Message -ForegroundColor Red
    Read-Host 'Press Enter to return to the unpacman installer' | Out-Null
    exit 1
}
'@
}

function Start-UnpacmanWslSetup {
    param([switch]$RuntimeOnly)
    Assert-UnpacmanSetupSpace
    if ($RuntimeOnly) {
        $featureDisabled = Test-UnpacmanVirtualMachinePlatformDisabled
        $msi = $null
        if (Test-UnpacmanWslRuntimeMissing) { $msi = Get-UnpacmanWslMsi }
        if (-not $msi -and -not $featureDisabled) {
            throw 'WSL is installed but cannot start. Restart Windows and open this installer again. If it still fails, send the WSL error shown above.'
        }
        $logs = Join-Path (Get-UnpacmanSetupFolder) ('logs\' + [guid]::NewGuid().ToString('N'))
        $command = Get-UnpacmanRuntimeCommand -Msi $msi.Path -Sha256 $msi.Sha256 -EnableFeature $featureDisabled -LogFolder $logs
        $encoded = [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($command))
        Write-Host 'Windows needs administrator approval for WSL itself. Approve the next prompt.'
        Write-Host 'Ubuntu and unpacman will be installed under your current Windows account.'
        $powershell = Join-Path $env:WINDIR 'System32\WindowsPowerShell\v1.0\powershell.exe'
        try {
            $process = Start-Process -FilePath $powershell -ArgumentList '-NoProfile', '-EncodedCommand', $encoded -Verb RunAs -WindowStyle Normal -WorkingDirectory $env:WINDIR -Wait -PassThru
        } catch {
            throw "Windows setup was not started. If you cancelled administrator approval, open the installer again when ready. $($_.Exception.Message)"
        }
        if ($process.ExitCode -notin @(0, 3010)) {
            # Only request a restart after a failed setup when Windows actually
            # has a pending component change (for example, a partial success).
            if (Test-Path -LiteralPath 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Component Based Servicing\RebootPending') {
                Set-UnpacmanRestartRequired
                Write-UnpacmanRestartInstructions
            }
            throw "Windows setup stopped. See the error in its setup window. Logs: $logs."
        }
        Set-UnpacmanRestartRequired
        Write-UnpacmanRestartInstructions
        return
    }
    Write-Host 'Downloading Ubuntu for your Windows account. Keep this window open.'
    $folder = Get-UnpacmanSetupFolder
    $null = New-Item -ItemType Directory -Path $folder -Force
    Set-Content -LiteralPath (Join-Path $folder 'ubuntu-setup.txt') -Value 'Ubuntu'
    & wsl.exe --install -d Ubuntu --no-launch --web-download | Out-Host
    if ($LASTEXITCODE -eq 3010) {
        Set-UnpacmanRestartRequired
        Write-UnpacmanRestartInstructions
        return
    }
    if ($LASTEXITCODE -ne 0) { throw "Ubuntu download/setup failed with code $LASTEXITCODE. See the error above, then open this installer again." }
    Complete-UnpacmanUbuntuSetup 'Ubuntu'
    $listed = Invoke-UnpacmanWslCapture @('--list', '--verbose')
    if ($listed.ExitCode -ne 0) { throw "Ubuntu setup could not be checked. $($listed.Text)" }
    return (Select-UnpacmanUbuntu @(ConvertFrom-UnpacmanWslList $listed.Text) 'Ubuntu')
}

function Complete-UnpacmanUbuntuSetup {
    param([string]$Name)
    $marker = Join-Path (Get-UnpacmanSetupFolder) 'ubuntu-setup.txt'
    if (-not (Test-Path -LiteralPath $marker) -or (Get-Content -LiteralPath $marker -Raw).Trim() -ne $Name) { return }
    Write-Host ''
    Write-Host 'Ubuntu will now ask you to choose a Linux username and password.'
    Write-Host 'No characters appear while you type the password; that is normal.'
    Write-Host 'When setup finishes and you see a prompt ending in $, type exit and press Enter.' -ForegroundColor Yellow
    Write-Host 'That returns here so unpacman can finish installing.'
    & wsl.exe --distribution $Name | Out-Host
    if ($LASTEXITCODE -ne 0) { throw 'Ubuntu user setup was interrupted. Open this installer again to continue.' }
    Assert-UnpacmanUbuntu ([pscustomobject]@{Name=$Name;Version=2})
    Remove-Item -LiteralPath $marker -Force
}

function Test-UnpacmanElevated {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = New-Object Security.Principal.WindowsPrincipal($identity)
    return $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

function Test-UnpacmanVirtualMachinePlatformDisabled {
    # A failed runtime install can leave wsl.exe unable to answer --list at
    # all. Check WSL 2's Windows prerequisite before relying on that command.
    try {
        $feature = Get-CimInstance Win32_OptionalFeature -Filter "Name='VirtualMachinePlatform'" -ErrorAction Stop
        return ($null -ne $feature -and $feature.InstallState -eq 2)
    } catch {
        # If the feature query is unavailable, use the existing WSL probe.
        return $false
    }
}

function Test-UnpacmanWslRuntimeMissing {
    try {
        # A leftover AppX registration is not proof that the runtime installed.
        # Also recognize a working older, inbox WSL installation.
        return @(Get-CimInstance Win32_Service -Filter "Name='WslService' OR Name='LxssManager'" -ErrorAction Stop).Count -eq 0
    } catch { return $false }
}

function Assert-UnpacmanWindows {
    $version = [version](Get-CimInstance Win32_OperatingSystem -ErrorAction Stop).Version
    if ($version.Build -lt 19041 -or -not [Environment]::Is64BitOperatingSystem -or -not [Environment]::Is64BitProcess) {
        throw 'Use 64-bit Windows PowerShell on Windows 11 or Windows 10 version 2004 (build 19041) or newer.'
    }
}

function Assert-UnpacmanUbuntu {
    param([object]$Entry)
    if ($Entry.Version -ne 2) {
        throw "'$($Entry.Name)' uses WSL 1. In PowerShell run: wsl --set-version `"$($Entry.Name)`" 2. Then rerun this installer."
    }
    $os = Invoke-UnpacmanWslCapture @('--distribution', $Entry.Name, '--exec', 'cat', '/etc/os-release')
    if ($os.ExitCode -ne 0) { throw "Ubuntu could not start. Open '$($Entry.Name)' and complete its first-time setup, then retry. $($os.Text)" }
    if ($os.Text -notmatch '(?m)^ID="?ubuntu"?\r?$') { throw "'$($Entry.Name)' is not Ubuntu. Select an Ubuntu distribution with -Distribution." }
    $uid = Invoke-UnpacmanWslCapture @('--distribution', $Entry.Name, '--exec', 'id', '-u')
    if ($uid.ExitCode -ne 0 -or $uid.Text.Trim() -notmatch '^\d+$') { throw "Could not check the Ubuntu user: $($uid.Text)" }
    if ($uid.Text.Trim() -eq '0') {
        throw "Ubuntu is opening as root. Complete Ubuntu's user setup and set a normal user as its default, then rerun this installer. Do not install unpacman as root."
    }
}

function Get-UnpacmanLinuxPath {
    param([string]$Name, [string]$WindowsPath)
    $result = Invoke-UnpacmanWslCapture @('--distribution', $Name, '--exec', 'wslpath', '-u', $WindowsPath)
    if ($result.ExitCode -ne 0 -or -not $result.Text.Trim()) { throw "Ubuntu could not access the installer folder: $($result.Text)" }
    return $result.Text.Trim()
}

function Get-UnpacmanDownload {
    param([uri]$Uri, [string]$Destination)
    if ($Uri.Scheme -ne 'https' -and -not ($Uri.Scheme -eq 'http' -and $Uri.IsLoopback)) {
        throw 'Installer downloads require HTTPS (or localhost for testing).'
    }
    [Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12
    Invoke-WebRequest -UseBasicParsing -Uri $Uri -OutFile $Destination -TimeoutSec 120 -ErrorAction Stop
    if ((Get-Item -LiteralPath $Destination).Length -eq 0) { throw 'The downloaded installer is empty.' }
    $firstLine = Get-Content -LiteralPath $Destination -TotalCount 1
    if ($firstLine -notmatch '^#!/(usr/)?bin/(env bash|bash)\s*$') { throw 'The download is not a Bash installer. Nothing was run.' }
}

function Start-UnpacmanLinuxInstaller {
    param([string]$Name, [string]$Script, [string]$Repo, [string]$Upstream, [bool]$SkipOpen, [bool]$SkipShortcut)
    $arguments = @('--distribution', $Name, '--exec', 'env')
    if ($Repo) { $arguments += "UNPACMAN_REPO=$Repo" }
    if ($Upstream) { $arguments += "UNPACMAN_FOLLOW=$Upstream" }
    if ($SkipOpen) { $arguments += 'UNPACMAN_NO_OPEN=1' }
    if ($SkipShortcut) { $arguments += 'UNPACMAN_NO_SHORTCUT=1' }
    $arguments += @('bash', $Script)
    # Keep stdin attached: sudo needs the person's password, and the installed
    # application stays in this window until they quit it.
    & wsl.exe @arguments
    if ($LASTEXITCODE -ne 0) { throw "The Ubuntu installer exited with code $LASTEXITCODE. Fix the error above and run the same installer again." }
}

function Install-Unpacman {
    param([string]$Requested, [string]$Package, [uri]$Url, [string]$Repo, [string]$Upstream, [bool]$SkipOpen, [bool]$SkipShortcut, [string]$LocalScript)
    $ErrorActionPreference = 'Stop'
    $windowsUser = [Security.Principal.WindowsIdentity]::GetCurrent().Name
    if (Test-UnpacmanElevated) {
        throw "This window belongs to the administrator account '$windowsUser'. Close it, return to your usual desktop, and double-click Install unpacman.cmd in the extracted folder. Use ordinary Windows PowerShell for a command-line install. Administrator approval is requested separately for WSL itself."
    }
    Write-Host 'UNPACMAN SETUP'
    Write-Host "Windows account: $windowsUser"
    # Validate extraction before requesting any machine-wide changes.
    if ($Package) {
        $folder = (Resolve-Path -LiteralPath $Package -ErrorAction Stop).Path
        foreach ($file in @('install.sh', 'install-unpacman-wsl.sh', 'unpacman.bundle')) {
            if (-not (Test-Path -LiteralPath (Join-Path $folder $file) -PathType Leaf)) { throw "The package is incomplete: $file is missing. Right-click the ZIP, choose Extract All, then open Install unpacman.cmd from the extracted folder." }
        }
    }
    Assert-UnpacmanWindows
    Write-Host ''
    Write-Host '1 of 3 - Windows requirements'
    if (Test-UnpacmanRestartRequired) { Write-UnpacmanRestartInstructions; return }
    if (-not (Get-Command wsl.exe -ErrorAction SilentlyContinue)) { throw 'Windows could not find wsl.exe. Install Windows updates, restart, and open this installer again.' }
    if ((Test-UnpacmanVirtualMachinePlatformDisabled) -or (Test-UnpacmanWslRuntimeMissing)) {
        Start-UnpacmanWslSetup -RuntimeOnly
        return
    }
    Write-Host 'Checking WSL (up to 30 seconds).'
    $listed = Invoke-UnpacmanWslCapture @('--list', '--verbose')
    if ($listed.ExitCode -ne 0) {
        # Recognized missing prerequisites only. WSL versions differ in whether
        # an empty distribution list succeeds; newer Windows also has a stub
        # wsl.exe before its runtime is installed.
        if ($listed.Text -match 'WSL_E_WSL_OPTIONAL_COMPONENT_REQUIRED|WSL_E_VIRTUAL_MACHINE_PLATFORM_REQUIRED|Windows Subsystem for Linux is not installed') {
            Start-UnpacmanWslSetup -RuntimeOnly
            return
        }
        if ($listed.Text -match 'WSL_E_DEFAULT_DISTRO_NOT_FOUND|Windows Subsystem for Linux has no installed distributions') {
            $listed = [pscustomobject]@{ExitCode=0;Text=''}
        } else {
            # Do not treat an arbitrary service/access failure as an absent WSL.
            throw "WSL could not list its distributions (exit code $($listed.ExitCode)). $($listed.Text) Restart Windows and try this installer again. If it still fails, send this error with your feedback."
        }
    }
    Write-Host 'Windows requirements are ready.'
    Write-Host ''
    Write-Host '2 of 3 - Ubuntu setup'
    $entry = Select-UnpacmanUbuntu @(ConvertFrom-UnpacmanWslList $listed.Text) $Requested
    if ($null -eq $entry) {
        $entry = Start-UnpacmanWslSetup
        if ($null -eq $entry) { return }
    } else {
        Complete-UnpacmanUbuntuSetup $entry.Name
    }
    Assert-UnpacmanUbuntu $entry
    Write-Host 'Ubuntu is ready.'
    Write-Host ''
    Write-Host '3 of 3 - Install unpacman'
    Write-Host "Installing unpacman in $($entry.Name) (WSL 2)."
    $temporary = $null
    try {
        if ($Package) {
            $scriptPath = Join-Path $folder 'install.sh'
        } elseif ($LocalScript) {
            # A checkout/source ZIP tests the shell installer beside this file.
            $scriptPath = (Resolve-Path -LiteralPath $LocalScript -ErrorAction Stop).Path
        } else {
            $temporary = Join-Path ([IO.Path]::GetTempPath()) ('unpacman-install-' + [guid]::NewGuid().ToString('N'))
            New-Item -ItemType Directory -Path $temporary | Out-Null
            $scriptPath = Join-Path $temporary 'install-unpacman-wsl.sh'
            Get-UnpacmanDownload $Url $scriptPath
        }
        $linuxPath = Get-UnpacmanLinuxPath $entry.Name $scriptPath
        Start-UnpacmanLinuxInstaller $entry.Name $linuxPath $Repo $Upstream $SkipOpen $SkipShortcut
    } finally {
        if ($temporary) {
            # Only the exact file and empty directory created by this run.
            Remove-Item -LiteralPath (Join-Path $temporary 'install-unpacman-wsl.sh') -Force -ErrorAction SilentlyContinue
            Remove-Item -LiteralPath $temporary -Force -ErrorAction SilentlyContinue
        }
    }
}

# Dot-sourcing exposes functions for tests without starting an installation.
if ($MyInvocation.InvocationName -ne '.') {
    if (-not $PackageDirectory -and $PSScriptRoot -and (Test-Path -LiteralPath (Join-Path $PSScriptRoot 'unpacman.bundle'))) {
        $PackageDirectory = $PSScriptRoot
    }
    $localScript = $null
    if ($PSScriptRoot -and (Test-Path -LiteralPath (Join-Path $PSScriptRoot 'install-unpacman-wsl.sh'))) {
        $localScript = Join-Path $PSScriptRoot 'install-unpacman-wsl.sh'
    }
    try {
        Install-Unpacman -Requested $Distribution -Package $PackageDirectory -Url $InstallerUrl -Repo $Repository -Upstream $Follow -SkipOpen $NoOpen -SkipShortcut $NoShortcut -LocalScript $localScript
    } catch {
        Write-Host ''
        Write-Host 'SETUP STOPPED' -ForegroundColor Red
        Write-Host $_.Exception.Message
        exit 1
    }
}
