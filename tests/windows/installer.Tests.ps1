$installer = Join-Path $PSScriptRoot '..\..\install-unpacman.ps1'
. $installer

Describe 'Choosing Ubuntu without changing the WSL default' {
    $listing = "  NAME                   STATE           VERSION`n* docker-desktop         Stopped         2`n  Ubuntu                 Running         2`n  Ubuntu-24.04            Stopped         2"
    It 'parses names, localized states, versions and the default marker' {
        $entries = @(ConvertFrom-UnpacmanWslList $listing.Replace('Running', 'En cours'))
        $entries.Count | Should Be 3
        $entries[0].Name | Should Be 'docker-desktop'
        $entries[0].Default | Should Be $true
        $entries[1].Name | Should Be 'Ubuntu'
        $entries[1].Default | Should Be $false
        $entries[1].Version | Should Be 2
    }
    It 'handles NUL characters in WSL output' {
        $nul = [string][char]0
        $text = ($listing.ToCharArray() | ForEach-Object { [string]$_ + $nul }) -join ''
        @(ConvertFrom-UnpacmanWslList $text).Count | Should Be 3
    }
    It 'selects Ubuntu when Docker is the default' {
        (Select-UnpacmanUbuntu @(ConvertFrom-UnpacmanWslList $listing)).Name | Should Be 'Ubuntu'
    }
    It 'recognizes a versioned Ubuntu installation' {
        (Select-UnpacmanUbuntu @([pscustomobject]@{Name='Ubuntu-24.04';Version=2;Default=$false})).Name | Should Be 'Ubuntu-24.04'
    }
    It 'accepts an explicitly named test distribution' {
        (Select-UnpacmanUbuntu @([pscustomobject]@{Name='UnpacmanInstallerTest';Version=2;Default=$false}) 'UnpacmanInstallerTest').Name | Should Be 'UnpacmanInstallerTest'
    }
    It 'does not guess between multiple versioned Ubuntu distributions' {
        $entries = @([pscustomobject]@{Name='Ubuntu-22.04';Default=$false}, [pscustomobject]@{Name='Ubuntu-24.04';Default=$false})
        { Select-UnpacmanUbuntu $entries } | Should Throw 'Several Ubuntu'
    }
    It 'uses the Ubuntu default when multiple versioned distributions exist' {
        $entries = @([pscustomobject]@{Name='Ubuntu-22.04';Default=$false}, [pscustomobject]@{Name='Ubuntu-24.04';Default=$true})
        (Select-UnpacmanUbuntu $entries).Name | Should Be 'Ubuntu-24.04'
    }
    It 'does not fall back when an explicit name is wrong' {
        { Select-UnpacmanUbuntu @(ConvertFrom-UnpacmanWslList $listing) 'Missing' } | Should Throw 'was not found'
    }
    It 'requests setup when only Docker exists' {
        Select-UnpacmanUbuntu @([pscustomobject]@{Name='docker-desktop';Version=2;Default=$true}) | Should BeNullOrEmpty
    }
}

Describe 'Ubuntu prerequisites' {
    BeforeEach {
        Mock Invoke-UnpacmanWslCapture {
            [pscustomobject]@{ExitCode=0;Text="NAME=Ubuntu`nID=ubuntu"}
        } -ParameterFilter { $Arguments -contains '/etc/os-release' }
        Mock Invoke-UnpacmanWslCapture { [pscustomobject]@{ExitCode=0;Text='1000'} } -ParameterFilter { $Arguments -contains 'id' }
    }
    It 'accepts WSL 2 Ubuntu with a normal user' {
        { Assert-UnpacmanUbuntu ([pscustomobject]@{Name='Ubuntu';Version=2}) } | Should Not Throw
    }
    It 'refuses WSL 1 before launching Linux' {
        { Assert-UnpacmanUbuntu ([pscustomobject]@{Name='Ubuntu';Version=1}) } | Should Throw 'WSL 1'
        Assert-MockCalled Invoke-UnpacmanWslCapture -Times 0 -Exactly -Scope It
    }
    It 'refuses root rather than installing into the wrong home' {
        Mock Invoke-UnpacmanWslCapture { [pscustomobject]@{ExitCode=0;Text='0'} } -ParameterFilter { $Arguments -contains 'id' }
        { Assert-UnpacmanUbuntu ([pscustomobject]@{Name='Ubuntu';Version=2}) } | Should Throw 'opening as root'
    }
    It 'checks the OS of an explicitly selected distribution' {
        Mock Invoke-UnpacmanWslCapture { [pscustomobject]@{ExitCode=0;Text='ID=alpine'} } -ParameterFilter { $Arguments -contains '/etc/os-release' }
        { Assert-UnpacmanUbuntu ([pscustomobject]@{Name='Custom';Version=2}) } | Should Throw 'not Ubuntu'
    }
    It 'explains an incomplete first-time setup' {
        Mock Invoke-UnpacmanWslCapture { [pscustomobject]@{ExitCode=1;Text='could not start'} } -ParameterFilter { $Arguments -contains '/etc/os-release' }
        { Assert-UnpacmanUbuntu ([pscustomobject]@{Name='Ubuntu';Version=2}) } | Should Throw 'first-time setup'
    }
}

Describe 'WSL setup and Windows account ownership' {
    BeforeEach {
        Mock Read-Host { '' }
        Mock Invoke-UnpacmanWindowsRestart { throw 'Unexpected restart during an installer test' }
        Mock Start-Process { [pscustomobject]@{ExitCode=0} }
        Mock Assert-UnpacmanSetupSpace {}
        Mock Test-UnpacmanVirtualMachinePlatformDisabled { $true }
        Mock Test-UnpacmanWslRuntimeMissing { $true }
        Mock Get-UnpacmanWslMsi { [pscustomobject]@{Path='C:\cache\wsl.msi';Sha256='testhash'} }
        Mock Get-UnpacmanSetupFolder { $TestDrive }
        Mock Set-UnpacmanRestartRequired {}
        Mock Test-Path { $false } -ParameterFilter { $LiteralPath -like 'HKLM:*RebootPending' }
        Mock Complete-UnpacmanUbuntuSetup {}
        Mock Invoke-UnpacmanWslCapture { [pscustomobject]@{ExitCode=0;Text="  NAME          STATE       VERSION`n* Ubuntu        Running     2"} }
        Mock wsl.exe { $script:setupArguments = @($args); $global:LASTEXITCODE = 0 }
        Mock Write-Host {}
        $script:setupArguments = @()
    }
    It 'elevates only the runtime and keeps the diagnostic window visible' {
        Start-UnpacmanWslSetup -RuntimeOnly
        Assert-MockCalled Start-Process -Times 1 -Exactly -Scope It -ParameterFilter {
            $child = [Text.Encoding]::Unicode.GetString([Convert]::FromBase64String($ArgumentList[-1]))
            $tokens = $null; $errors = $null
            $null = [Management.Automation.Language.Parser]::ParseInput($child, [ref]$tokens, [ref]$errors)
            $errors.Count -eq 0 -and
            $FilePath -eq (Join-Path $env:WINDIR 'System32\WindowsPowerShell\v1.0\powershell.exe') -and
            $Verb -eq 'RunAs' -and $WindowStyle -eq 'Normal' -and $Wait -and $PassThru -and
            $child.Contains('msiexec.exe') -and $child.Contains('dism.exe') -and
            -not $child.Contains('wsl.exe') -and $child.Contains('Read-Host') -and
            $child.Contains('exit $result')
        }
        Assert-MockCalled wsl.exe -Times 0 -Exactly -Scope It
    }
    It 'surfaces a failed runtime install without installing Ubuntu' {
        Mock Start-Process { [pscustomobject]@{ExitCode=1} }
        { Start-UnpacmanWslSetup -RuntimeOnly } | Should Throw 'Windows setup stopped'
        Assert-MockCalled wsl.exe -Times 0 -Exactly -Scope It
        Assert-MockCalled Set-UnpacmanRestartRequired -Times 0 -Exactly -Scope It
    }
    It 'accepts a runtime install that requires a restart' {
        Mock Start-Process { [pscustomobject]@{ExitCode=3010} }
        { Start-UnpacmanWslSetup -RuntimeOnly } | Should Not Throw
        Assert-MockCalled Set-UnpacmanRestartRequired -Times 1 -Exactly -Scope It
        Assert-MockCalled wsl.exe -Times 0 -Exactly -Scope It
    }
    It 'does not continue if administrator approval is cancelled' {
        Mock Start-Process { throw 'The operation was canceled by the user.' }
        { Start-UnpacmanWslSetup -RuntimeOnly } | Should Throw 'canceled by the user'
        Assert-MockCalled wsl.exe -Times 0 -Exactly -Scope It
        Assert-MockCalled Set-UnpacmanRestartRequired -Times 0 -Exactly -Scope It
    }
    It 'installs Ubuntu in the calling account without elevation' {
        (Start-UnpacmanWslSetup).Name | Should Be 'Ubuntu'
        ($script:setupArguments -join ' ') | Should Be '--install -d Ubuntu --no-launch --web-download'
        Assert-MockCalled wsl.exe -Times 1 -Exactly -Scope It
        Assert-MockCalled Start-Process -Times 0 -Exactly -Scope It
        Assert-MockCalled Complete-UnpacmanUbuntuSetup -Times 1 -Exactly -Scope It
    }
    It 'surfaces a failed Ubuntu install' {
        Mock wsl.exe { $global:LASTEXITCODE = 1 }
        { Start-UnpacmanWslSetup } | Should Throw 'Ubuntu download/setup failed with code 1'
        Assert-MockCalled Start-Process -Times 0 -Exactly -Scope It
    }
    It 'stops for low disk space before any download or elevation' {
        Mock Assert-UnpacmanSetupSpace { throw 'Free at least 10 GB' }
        { Start-UnpacmanWslSetup -RuntimeOnly } | Should Throw '10 GB'
        Assert-MockCalled Get-UnpacmanWslMsi -Times 0 -Exactly -Scope It
        Assert-MockCalled Start-Process -Times 0 -Exactly -Scope It
    }
    It 'does not replace an installed runtime when only the Windows feature needs enabling' {
        Mock Test-UnpacmanWslRuntimeMissing { $false }
        Start-UnpacmanWslSetup -RuntimeOnly
        Assert-MockCalled Get-UnpacmanWslMsi -Times 0 -Exactly -Scope It
        Assert-MockCalled Start-Process -Times 1 -Exactly -Scope It
    }
    It 'waits for a requested Ubuntu reboot before starting user setup' {
        Mock wsl.exe { $global:LASTEXITCODE = 3010 }
        Start-UnpacmanWslSetup | Should BeNullOrEmpty
        Assert-MockCalled Complete-UnpacmanUbuntuSetup -Times 0 -Exactly -Scope It
        Assert-MockCalled Set-UnpacmanRestartRequired -Times 1 -Exactly -Scope It
    }
}

Describe 'Windows prerequisite detection' {
    It 'recognizes a disabled Virtual Machine Platform' {
        Mock Get-CimInstance { [pscustomobject]@{InstallState=2} }
        Test-UnpacmanVirtualMachinePlatformDisabled | Should Be $true
        Assert-MockCalled Get-CimInstance -Times 1 -Exactly -Scope It -ParameterFilter {
            $ClassName -eq 'Win32_OptionalFeature' -and $Filter -eq "Name='VirtualMachinePlatform'"
        }
    }
    It 'does not request setup for an enabled feature' {
        Mock Get-CimInstance { [pscustomobject]@{InstallState=1} }
        Test-UnpacmanVirtualMachinePlatformDisabled | Should Be $false
    }
    It 'does not mistake a failed query for a disabled feature' {
        Mock Get-CimInstance { throw 'Access denied' }
        Test-UnpacmanVirtualMachinePlatformDisabled | Should Be $false
    }
    It 'does not guess when the feature state is unknown' {
        Mock Get-CimInstance { [pscustomobject]@{InstallState=4} }
        Test-UnpacmanVirtualMachinePlatformDisabled | Should Be $false
    }
}

Describe 'Download and package entry points' {
    BeforeEach {
        Mock Read-Host { '' }
        Mock Invoke-UnpacmanWindowsRestart { throw 'Unexpected restart during an installer test' }
        Mock Test-UnpacmanElevated { $false }
        Mock Assert-UnpacmanWindows {}
        Mock Test-UnpacmanWslRuntimeMissing { $false }
        Mock Test-UnpacmanRestartRequired { $false }
        Mock Complete-UnpacmanUbuntuSetup {}
        Mock Test-UnpacmanVirtualMachinePlatformDisabled { $false }
        Mock Invoke-UnpacmanWslCapture { [pscustomobject]@{ExitCode=0;Text="  NAME          STATE       VERSION`n* Ubuntu        Running     2"} }
        Mock Assert-UnpacmanUbuntu {}
        Mock Get-UnpacmanLinuxPath { '/mnt/c/test folder/install.sh' }
        Mock Get-UnpacmanDownload { Set-Content -LiteralPath $Destination -Value '#!/bin/bash' }
        Mock Start-UnpacmanLinuxInstaller {}
        Mock Start-UnpacmanWslSetup {}
        Mock Write-Host {}
    }
    It 'runs the online installer and removes its temporary download' {
        Install-Unpacman -Url 'http://127.0.0.1:8876/install.sh' -SkipOpen $true
        Assert-MockCalled Get-UnpacmanDownload -Times 1 -Exactly -Scope It
        Assert-MockCalled Start-UnpacmanLinuxInstaller -Times 1 -Exactly -Scope It -ParameterFilter { $Name -eq 'Ubuntu' -and $SkipOpen }
        Assert-MockCalled Start-UnpacmanWslSetup -Times 0 -Exactly -Scope It
    }
    It 'refuses an elevated parent before any WSL or download action' {
        Mock Test-UnpacmanElevated { $true }
        { Install-Unpacman } | Should Throw 'ordinary Windows PowerShell'
        Assert-MockCalled Invoke-UnpacmanWslCapture -Times 0 -Exactly -Scope It
        Assert-MockCalled Get-UnpacmanDownload -Times 0 -Exactly -Scope It
        Assert-MockCalled Start-UnpacmanWslSetup -Times 0 -Exactly -Scope It
    }
    It 'does not execute a failed download' {
        Mock Get-UnpacmanDownload { throw 'download failed' }
        { Install-Unpacman -Url 'http://127.0.0.1:8876/install.sh' } | Should Throw 'download failed'
        Assert-MockCalled Start-UnpacmanLinuxInstaller -Times 0 -Exactly -Scope It
    }
    It 'uses the ZIP locally without downloading the installer' {
        $folder = Join-Path $TestDrive 'package with spaces'
        New-Item -ItemType Directory -Path $folder | Out-Null
        'install.sh','install-unpacman-wsl.sh','unpacman.bundle' | ForEach-Object { Set-Content -LiteralPath (Join-Path $folder $_) -Value 'test' }
        Install-Unpacman -Package $folder
        Assert-MockCalled Start-UnpacmanLinuxInstaller -Times 1 -Exactly -Scope It
        Assert-MockCalled Get-UnpacmanDownload -Times 0 -Exactly -Scope It
    }
    It 'uses the checkout shell installer without downloading another version' {
        $script = Join-Path $TestDrive 'install-unpacman-wsl.sh'
        Set-Content -LiteralPath $script -Value '#!/bin/bash'
        Install-Unpacman -LocalScript $script
        Assert-MockCalled Start-UnpacmanLinuxInstaller -Times 1 -Exactly -Scope It
        Assert-MockCalled Get-UnpacmanDownload -Times 0 -Exactly -Scope It
    }
    It 'refuses an incompletely extracted package' {
        Mock Test-UnpacmanVirtualMachinePlatformDisabled { $true }
        { Install-Unpacman -Package $TestDrive } | Should Throw 'package is incomplete'
        Assert-MockCalled Assert-UnpacmanWindows -Times 0 -Exactly -Scope It
        Assert-MockCalled Start-UnpacmanLinuxInstaller -Times 0 -Exactly -Scope It
        Assert-MockCalled Start-UnpacmanWslSetup -Times 0 -Exactly -Scope It
    }
    It 'waits for a recorded restart without invoking WSL or installing again' {
        Mock Test-UnpacmanRestartRequired { $true }
        Install-Unpacman
        Assert-MockCalled Invoke-UnpacmanWslCapture -Times 0 -Exactly -Scope It
        Assert-MockCalled Start-UnpacmanWslSetup -Times 0 -Exactly -Scope It
        Assert-MockCalled Start-UnpacmanLinuxInstaller -Times 0 -Exactly -Scope It
    }
    It 'continues into unpacman after Ubuntu setup succeeds' {
        Mock Invoke-UnpacmanWslCapture { [pscustomobject]@{ExitCode=0;Text=''} }
        Mock Start-UnpacmanWslSetup { [pscustomobject]@{Name='Ubuntu';Version=2} }
        Install-Unpacman -Url 'http://127.0.0.1:8876/install.sh'
        Assert-MockCalled Assert-UnpacmanUbuntu -Times 1 -Exactly -Scope It
        Assert-MockCalled Start-UnpacmanLinuxInstaller -Times 1 -Exactly -Scope It
    }
    It 'repairs an absent runtime before relying on a leftover AppX launcher' {
        Mock Test-UnpacmanWslRuntimeMissing { $true }
        Install-Unpacman
        Assert-MockCalled Invoke-UnpacmanWslCapture -Times 0 -Exactly -Scope It
        Assert-MockCalled Start-UnpacmanWslSetup -Times 1 -Exactly -Scope It -ParameterFilter { $RuntimeOnly }
    }
    It 'guides Ubuntu setup without running the app installer' {
        Mock Invoke-UnpacmanWslCapture { [pscustomobject]@{ExitCode=0;Text="  NAME          STATE       VERSION`n* docker-desktop     Stopped     2"} }
        Install-Unpacman
        Assert-MockCalled Start-UnpacmanWslSetup -Times 1 -Exactly -Scope It -ParameterFilter { -not $RuntimeOnly }
        Assert-MockCalled Start-UnpacmanLinuxInstaller -Times 0 -Exactly -Scope It
    }
    It 'does not mistake a WSL service error for an absent Ubuntu' {
        Mock Invoke-UnpacmanWslCapture { [pscustomobject]@{ExitCode=1;Text='E_ACCESSDENIED'} }
        { Install-Unpacman } | Should Throw 'E_ACCESSDENIED'
        Assert-MockCalled Start-UnpacmanWslSetup -Times 0 -Exactly -Scope It
    }
    It 'requests runtime setup before probing a partial install with its Windows feature disabled' {
        Mock Test-UnpacmanVirtualMachinePlatformDisabled { $true }
        Mock Invoke-UnpacmanWslCapture { throw 'A partial WSL installation cannot answer --list' }
        Install-Unpacman
        Assert-MockCalled Start-UnpacmanWslSetup -Times 1 -Exactly -Scope It -ParameterFilter { $RuntimeOnly }
        Assert-MockCalled Invoke-UnpacmanWslCapture -Times 0 -Exactly -Scope It
        Assert-MockCalled Start-UnpacmanLinuxInstaller -Times 0 -Exactly -Scope It
    }
    It 'includes the exit code when WSL fails without a diagnostic' {
        Mock Invoke-UnpacmanWslCapture { [pscustomobject]@{ExitCode=-1;Text=''} }
        { Install-Unpacman } | Should Throw 'exit code -1'
        Assert-MockCalled Start-UnpacmanWslSetup -Times 0 -Exactly -Scope It
    }
    It 'requests setup when Windows reports the WSL runtime is missing' {
        Mock Invoke-UnpacmanWslCapture { [pscustomobject]@{ExitCode=1;Text='The Windows Subsystem for Linux is not installed. You can install it by running wsl.exe --install.'} }
        Install-Unpacman
        Assert-MockCalled Start-UnpacmanWslSetup -Times 1 -Exactly -Scope It -ParameterFilter { $RuntimeOnly }
        Assert-MockCalled Start-UnpacmanLinuxInstaller -Times 0 -Exactly -Scope It
    }
    It 'requests setup for a missing optional component regardless of language' {
        Mock Invoke-UnpacmanWslCapture { [pscustomobject]@{ExitCode=1;Text='Wsl/WSL_E_WSL_OPTIONAL_COMPONENT_REQUIRED'} }
        Install-Unpacman
        Assert-MockCalled Start-UnpacmanWslSetup -Times 1 -Exactly -Scope It -ParameterFilter { $RuntimeOnly }
    }
    It 'requests setup when an older WSL returns failure for an empty list' {
        Mock Invoke-UnpacmanWslCapture { [pscustomobject]@{ExitCode=1;Text='Windows Subsystem for Linux has no installed distributions.'} }
        Install-Unpacman
        Assert-MockCalled Start-UnpacmanWslSetup -Times 1 -Exactly -Scope It -ParameterFilter { -not $RuntimeOnly }
    }
    It 'installs Ubuntu for the current account for a localized empty list error' {
        Mock Invoke-UnpacmanWslCapture { [pscustomobject]@{ExitCode=1;Text='Wsl/WSL_E_DEFAULT_DISTRO_NOT_FOUND'} }
        Install-Unpacman
        Assert-MockCalled Start-UnpacmanWslSetup -Times 1 -Exactly -Scope It -ParameterFilter { -not $RuntimeOnly }
    }
    It 'surfaces a setup failure without continuing the installation' {
        Mock Invoke-UnpacmanWslCapture { [pscustomobject]@{ExitCode=0;Text=''} }
        Mock Start-UnpacmanWslSetup { throw 'setup failed' }
        { Install-Unpacman } | Should Throw 'setup failed'
        Assert-MockCalled Start-UnpacmanLinuxInstaller -Times 0 -Exactly -Scope It
    }
    It 'propagates a failed Linux installation' {
        Mock Start-UnpacmanLinuxInstaller { throw 'installer exited with code 1' }
        { Install-Unpacman -Url 'http://127.0.0.1:8876/install.sh' } | Should Throw 'exited with code 1'
    }
}

Describe 'Download validation' {
    It 'rejects insecure remote transport' {
        { Get-UnpacmanDownload 'http://example.com/install.sh' (Join-Path $TestDrive 'bad.sh') } | Should Throw 'require HTTPS'
    }
    It 'rejects an HTML response without executing it' {
        Mock Invoke-WebRequest { Set-Content -LiteralPath $OutFile -Value '<html>error</html>' }
        { Get-UnpacmanDownload 'https://example.com/install.sh' (Join-Path $TestDrive 'html.sh') } | Should Throw 'not a Bash installer'
    }
    It 'rejects an empty response' {
        Mock Invoke-WebRequest { [IO.File]::WriteAllText($OutFile, '') }
        { Get-UnpacmanDownload 'https://example.com/install.sh' (Join-Path $TestDrive 'empty.sh') } | Should Throw 'empty'
    }
}
