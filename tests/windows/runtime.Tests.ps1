. (Join-Path $PSScriptRoot '..\..\install-unpacman.ps1')

Describe 'Verified Microsoft runtime installer' {
    BeforeEach {
        Mock Write-Host {}
        Mock Get-FileHash { [pscustomobject]@{Hash='expected'} }
        Mock Get-AuthenticodeSignature { [pscustomobject]@{Status='Valid';SignerCertificate=[pscustomobject]@{Subject='CN=Microsoft Corporation, O=Microsoft Corporation, C=US'}} }
        Mock Start-Process { [pscustomobject]@{ExitCode=0} }
        Mock Start-Process { [pscustomobject]@{ExitCode=0} } -ParameterFilter { $FilePath -like '*\msiexec.exe' }
        Mock Start-Process { [pscustomobject]@{ExitCode=0} } -ParameterFilter { $FilePath -like '*\dism.exe' }
    }
    It 'installs the checked MSI and enables only the required feature without restarting Windows' {
        Invoke-UnpacmanRuntimeInstall 'C:\folder with spaces\wsl.msi' 'expected' $true $TestDrive | Should Be 3010
        Assert-MockCalled Get-FileHash -Times 1 -Exactly -Scope It
        Assert-MockCalled Get-AuthenticodeSignature -Times 1 -Exactly -Scope It
        Assert-MockCalled Start-Process -Times 1 -Exactly -Scope It -ParameterFilter {
            $FilePath -like '*\msiexec.exe' -and $Wait -and $PassThru -and
            $ArgumentList[1] -eq '"C:\folder with spaces\wsl.msi"' -and
            $ArgumentList -contains '/norestart' -and $ArgumentList -contains '/L*v'
        }
        Assert-MockCalled Start-Process -Times 1 -Exactly -Scope It -ParameterFilter {
            $FilePath -like '*\dism.exe' -and $NoNewWindow -and
            $ArgumentList -contains '/featurename:VirtualMachinePlatform' -and $ArgumentList -contains '/norestart'
        }
    }
    It 'rejects changed bytes before any elevated action' {
        { Invoke-UnpacmanRuntimeInstall 'C:\wsl.msi' 'different' $true $TestDrive } | Should Throw 'checksum'
        Assert-MockCalled Start-Process -Times 0 -Exactly -Scope It
    }
    It 'rejects an untrusted signer before any elevated action' {
        Mock Get-AuthenticodeSignature { [pscustomobject]@{Status='NotTrusted'} }
        { Invoke-UnpacmanRuntimeInstall 'C:\wsl.msi' 'expected' $true $TestDrive } | Should Throw 'valid Microsoft signature'
        Assert-MockCalled Start-Process -Times 0 -Exactly -Scope It
    }
    It 'rejects a valid signature from another publisher' {
        Mock Get-AuthenticodeSignature { [pscustomobject]@{Status='Valid';SignerCertificate=[pscustomobject]@{Subject='CN=Other, O=Another Corporation, C=US'}} }
        { Invoke-UnpacmanRuntimeInstall 'C:\wsl.msi' 'expected' $true $TestDrive } | Should Throw 'valid Microsoft signature'
        Assert-MockCalled Start-Process -Times 0 -Exactly -Scope It
    }
    It 'reports an MSI failure with its log location and does not continue' {
        Mock Start-Process { [pscustomobject]@{ExitCode=1603} } -ParameterFilter { $FilePath -like '*\msiexec.exe' }
        { Invoke-UnpacmanRuntimeInstall 'C:\wsl.msi' 'expected' $true $TestDrive } | Should Throw 'code 1603. Details:'
        Assert-MockCalled Start-Process -Times 0 -Exactly -Scope It -ParameterFilter { $FilePath -like '*\dism.exe' }
    }
    It 'reports a Windows feature failure with its log location' {
        Mock Start-Process { [pscustomobject]@{ExitCode=5} } -ParameterFilter { $FilePath -like '*\dism.exe' }
        { Invoke-UnpacmanRuntimeInstall '' '' $true $TestDrive } | Should Throw 'code 5. Details:'
    }
    It 'accepts an MSI restart request and skips a feature already enabled' {
        Mock Start-Process { [pscustomobject]@{ExitCode=3010} } -ParameterFilter { $FilePath -like '*\msiexec.exe' }
        Invoke-UnpacmanRuntimeInstall 'C:\wsl.msi' 'expected' $false $TestDrive | Should Be 3010
        Assert-MockCalled Start-Process -Times 0 -Exactly -Scope It -ParameterFilter { $FilePath -like '*\dism.exe' }
    }
    It 'enables a missing feature without reinstalling an existing runtime' {
        Invoke-UnpacmanRuntimeInstall '' '' $true $TestDrive | Should Be 3010
        Assert-MockCalled Start-Process -Times 0 -Exactly -Scope It -ParameterFilter { $FilePath -like '*\msiexec.exe' }
        Assert-MockCalled Get-FileHash -Times 0 -Exactly -Scope It
    }
    It 'transports unusual paths into the elevated process as data' {
        $path = 'C:\Users\O''Brien & $special\wsl.msi'
        $command = Get-UnpacmanRuntimeCommand $path 'expected' $true $TestDrive
        $tokens = $null; $errors = $null
        $null = [Management.Automation.Language.Parser]::ParseInput($command, [ref]$tokens, [ref]$errors)
        $errors.Count | Should Be 0
        $command.Contains($path) | Should Be $false
        $match = [regex]::Match($command, "FromBase64String\('([^']+)'\)")
        $data = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($match.Groups[1].Value)) | ConvertFrom-Json
        $data.Msi | Should Be $path
        $data.EnableFeature | Should Be $true
        $data.LogFolder | Should Be ([string]$TestDrive)
        $command.Contains('wsl.exe') | Should Be $false
    }
}

Describe 'Runtime downloads and cache' {
    BeforeEach {
        $script:runtimeCache = Join-Path $TestDrive ([guid]::NewGuid().ToString('N'))
        $null = New-Item -ItemType Directory -Path $script:runtimeCache
        Mock Get-UnpacmanSetupFolder { $script:runtimeCache }
        Mock Get-UnpacmanWslRelease { [pscustomobject]@{Name='test.msi';Sha256='expected';Url='https://example.com/test.msi'} }
        Mock Assert-UnpacmanMicrosoftMsi {}
        Mock Write-Host {}
        # Exercise the PowerShell fallback with a stand-in download; never curl.
        Mock Test-Path { $false } -ParameterFilter { $LiteralPath -like '*\curl.exe' }
        Mock Invoke-WebRequest { Set-Content -LiteralPath $OutFile -Value 'downloaded MSI stand-in' }
    }
    It 'checks a complete download before caching it' {
        $result = Get-UnpacmanWslMsi
        Test-Path -LiteralPath $result.Path | Should Be $true
        $result.Sha256 | Should Be 'expected'
        Assert-MockCalled Assert-UnpacmanMicrosoftMsi -Times 1 -Exactly -Scope It -ParameterFilter { $Path -like '*.part' -and $Sha256 -eq 'expected' }
        Test-Path -LiteralPath ($result.Path + '.part') | Should Be $false
    }
    It 'verifies and reuses the cached file without downloading again' {
        Set-Content -LiteralPath (Join-Path $script:runtimeCache 'test.msi') -Value 'cached'
        $null = Get-UnpacmanWslMsi
        Assert-MockCalled Invoke-WebRequest -Times 0 -Exactly -Scope It
        Assert-MockCalled Assert-UnpacmanMicrosoftMsi -Times 1 -Exactly -Scope It
    }
    It 'discards an interrupted partial download while preserving other files' {
        Set-Content -LiteralPath (Join-Path $script:runtimeCache 'keep.txt') -Value 'keep'
        Mock Invoke-WebRequest { Set-Content -LiteralPath $OutFile -Value 'partial'; throw 'Connection failed' }
        { Get-UnpacmanWslMsi } | Should Throw 'Connection failed'
        Test-Path -LiteralPath (Join-Path $script:runtimeCache 'test.msi.part') | Should Be $false
        Test-Path -LiteralPath (Join-Path $script:runtimeCache 'test.msi') | Should Be $false
        Get-Content -LiteralPath (Join-Path $script:runtimeCache 'keep.txt') | Should Be 'keep'
    }
    It 'does not cache a download that fails verification' {
        Mock Assert-UnpacmanMicrosoftMsi { throw 'checksum mismatch' }
        { Get-UnpacmanWslMsi } | Should Throw 'checksum mismatch'
        Test-Path -LiteralPath (Join-Path $script:runtimeCache 'test.msi') | Should Be $false
        Test-Path -LiteralPath (Join-Path $script:runtimeCache 'test.msi.part') | Should Be $false
    }
}

Describe 'Resume after restart and Ubuntu user setup' {
    BeforeEach {
        Mock Get-UnpacmanSetupFolder { $TestDrive }
        Mock Get-UnpacmanBootTime { 'first boot' }
        Mock Test-UnpacmanServicingRestartPending { $false }
        Mock Write-Host {}
        Mock wsl.exe { $global:LASTEXITCODE = 0 }
        Mock Assert-UnpacmanUbuntu {}
    }
    It 'keeps the restart request until the machine has actually restarted' {
        Set-UnpacmanRestartRequired
        Test-UnpacmanRestartRequired | Should Be $true
        Mock Get-UnpacmanBootTime { 'second boot' }
        Test-UnpacmanRestartRequired | Should Be $false
        Test-Path -LiteralPath (Join-Path $TestDrive 'restart.txt') | Should Be $false
    }
    It 'does not open or change the user setup of an existing Ubuntu' {
        Complete-UnpacmanUbuntuSetup 'Ubuntu'
        Assert-MockCalled wsl.exe -Times 0 -Exactly -Scope It
    }
    It 'finishes the new Ubuntu user setup before clearing its resume marker' {
        Set-Content -LiteralPath (Join-Path $TestDrive 'ubuntu-setup.txt') -Value 'Ubuntu'
        Complete-UnpacmanUbuntuSetup 'Ubuntu'
        Assert-MockCalled wsl.exe -Times 1 -Exactly -Scope It
        Assert-MockCalled Assert-UnpacmanUbuntu -Times 1 -Exactly -Scope It
        Test-Path -LiteralPath (Join-Path $TestDrive 'ubuntu-setup.txt') | Should Be $false
    }
    It 'retains the resume marker after an interrupted user setup' {
        Set-Content -LiteralPath (Join-Path $TestDrive 'ubuntu-setup.txt') -Value 'Ubuntu'
        Mock wsl.exe { $global:LASTEXITCODE = 1 }
        { Complete-UnpacmanUbuntuSetup 'Ubuntu' } | Should Throw 'interrupted'
        Test-Path -LiteralPath (Join-Path $TestDrive 'ubuntu-setup.txt') | Should Be $true
    }
    It 'retains the resume marker if Ubuntu still opens as root' {
        Set-Content -LiteralPath (Join-Path $TestDrive 'ubuntu-setup.txt') -Value 'Ubuntu'
        Mock Assert-UnpacmanUbuntu { throw 'Ubuntu is opening as root' }
        { Complete-UnpacmanUbuntuSetup 'Ubuntu' } | Should Throw 'opening as root'
        Test-Path -LiteralPath (Join-Path $TestDrive 'ubuntu-setup.txt') | Should Be $true
    }
    It 'does not apply a new Ubuntu marker to a different existing distribution' {
        Set-Content -LiteralPath (Join-Path $TestDrive 'ubuntu-setup.txt') -Value 'Ubuntu'
        Complete-UnpacmanUbuntuSetup 'Ubuntu-24.04'
        Assert-MockCalled wsl.exe -Times 0 -Exactly -Scope It
    }
}
