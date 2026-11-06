# Never requests a real restart. The interactive boundary is mocked; the
# native protocol test replaces only InitiateShutdownW with a recording stub.
. (Join-Path $PSScriptRoot '..\..\install-unpacman.ps1')

Describe 'Restart consent and recovery' {
    BeforeEach {
        Mock Write-Host {}
        Mock Read-Host { '' }
        Mock Invoke-UnpacmanWindowsRestart {}
        Mock Get-UnpacmanSetupFolder { $TestDrive }
        Mock Get-UnpacmanBootTime { 'before restart' }
        Mock Test-UnpacmanServicingRestartPending { $false }
    }
    It 'defers on Enter and retains the checkpoint' {
        Set-UnpacmanRestartRequired
        Write-UnpacmanRestartInstructions
        Assert-MockCalled Invoke-UnpacmanWindowsRestart -Times 0 -Exactly -Scope It
        Test-UnpacmanRestartRequired | Should Be $true
    }
    It 'does not treat another answer or a partial word as consent' {
        foreach ($answer in @('yes', 'R', 'restart', 'RESTART later', ' RESTART')) {
            $script:restartAnswer = $answer
            Mock Read-Host { $script:restartAnswer }
            Write-UnpacmanRestartInstructions
        }
        Assert-MockCalled Invoke-UnpacmanWindowsRestart -Times 0 -Exactly -Scope It
    }
    It 'defers safely when there is no interactive input' {
        Mock Read-Host { throw 'PowerShell is in NonInteractive mode' }
        { Write-UnpacmanRestartInstructions } | Should Not Throw
        Assert-MockCalled Invoke-UnpacmanWindowsRestart -Times 0 -Exactly -Scope It
    }
    It 'requests a restart only after the complete confirmation' {
        Mock Read-Host { 'RESTART' }
        Set-UnpacmanRestartRequired
        Write-UnpacmanRestartInstructions
        Assert-MockCalled Invoke-UnpacmanWindowsRestart -Times 1 -Exactly -Scope It
        Test-UnpacmanRestartRequired | Should Be $true
    }
    It 'reports a refused restart and preserves the checkpoint for a retry' {
        Mock Read-Host { 'RESTART' }
        Mock Invoke-UnpacmanWindowsRestart { throw 'Windows declined the restart request (code 1191): another user is signed in' }
        Set-UnpacmanRestartRequired
        { Write-UnpacmanRestartInstructions } | Should Throw 'code 1191'
        Test-UnpacmanRestartRequired | Should Be $true
    }
    It 'retains the checkpoint when Windows reboots but defers component changes' {
        Set-UnpacmanRestartRequired
        Mock Get-UnpacmanBootTime { 'after ordinary restart' }
        Mock Test-UnpacmanServicingRestartPending { $true }
        Test-UnpacmanRestartRequired | Should Be $true
        (Get-Content -LiteralPath (Join-Path $TestDrive 'restart.txt') -Raw).Trim() | Should Be 'before restart'
        Mock Test-UnpacmanServicingRestartPending { $false }
        Test-UnpacmanRestartRequired | Should Be $false
        Test-Path -LiteralPath (Join-Path $TestDrive 'restart.txt') | Should Be $false
    }
    It 'does not block an existing installation on unrelated pending updates' {
        Mock Test-UnpacmanServicingRestartPending { $true }
        Test-UnpacmanRestartRequired | Should Be $false
        Assert-MockCalled Test-UnpacmanServicingRestartPending -Times 0 -Exactly -Scope It
    }
    It 'records the checkpoint before offering the restart after WSL setup' {
        Mock Assert-UnpacmanSetupSpace {}
        Mock Test-UnpacmanVirtualMachinePlatformDisabled { $true }
        Mock Test-UnpacmanWslRuntimeMissing { $false }
        Mock Start-Process { [pscustomobject]@{ExitCode=3010} }
        Mock Read-Host {
            if (-not (Test-Path -LiteralPath (Join-Path $TestDrive 'restart.txt'))) { throw 'Missing restart checkpoint' }
            'RESTART'
        }
        Start-UnpacmanWslSetup -RuntimeOnly
        Assert-MockCalled Invoke-UnpacmanWindowsRestart -Times 1 -Exactly -Scope It
        Test-UnpacmanRestartRequired | Should Be $true
    }
    It 'stops before WSL or app installation while servicing is still pending' {
        Set-UnpacmanRestartRequired
        Mock Get-UnpacmanBootTime { 'after ordinary restart' }
        Mock Test-UnpacmanServicingRestartPending { $true }
        Mock Test-UnpacmanElevated { $false }
        Mock Assert-UnpacmanWindows {}
        Mock Invoke-UnpacmanWslCapture { throw 'Must not run WSL yet' }
        Mock Start-UnpacmanWslSetup { throw 'Must not install WSL again' }
        Mock Start-UnpacmanLinuxInstaller { throw 'Must not install the app yet' }
        Install-Unpacman
        Assert-MockCalled Read-Host -Times 1 -Exactly -Scope It
        Assert-MockCalled Invoke-UnpacmanWslCapture -Times 0 -Exactly -Scope It
        Assert-MockCalled Start-UnpacmanWslSetup -Times 0 -Exactly -Scope It
        Assert-MockCalled Start-UnpacmanLinuxInstaller -Times 0 -Exactly -Scope It
    }
}

Describe 'Native Windows restart protocol without a real shutdown' {
    It 'compiles the production native declaration without invoking it' {
        if (-not ('UnpacmanWindowsRestart' -as [type])) {
            Add-Type -TypeDefinition (Get-UnpacmanRestartTypeDefinition) -ErrorAction Stop
        }
        ('UnpacmanWindowsRestart' -as [type]) | Should Not BeNullOrEmpty
    }
    It 'requests pending updates without force, preserves errors and restores the callers privilege' {
        $source = (Get-UnpacmanRestartTypeDefinition).Replace("`r`n", "`n")
        $pattern = '(?s)\[DllImport\("advapi32\.dll", CharSet = CharSet\.Unicode, ExactSpelling = true\)\]\s+private static extern uint InitiateShutdownW\(\s*string machine, string message, uint gracePeriod, uint flags, uint reason\);'
        # Fail closed if the production boundary changes. NEVER call the
        # production shutdown function while checking the native wrapper.
        [regex]::Matches($source, $pattern).Count | Should Be 1
        if ([regex]::Matches($source, $pattern).Count -ne 1) { throw 'Cannot safely replace the shutdown boundary' }
        $stub = @'
public static uint LastFlags, LastGracePeriod, LastReason, Calls, Result;
public static string LastMachine;
private static uint InitiateShutdownW(string machine, string message, uint gracePeriod, uint flags, uint reason)
{
    Calls++;
    LastMachine = machine;
    LastFlags = flags;
    LastGracePeriod = gracePeriod;
    LastReason = reason;
    return Result;
}
'@
        $typeName = 'UnpacmanRestartProbe_' + [guid]::NewGuid().ToString('N')
        $source = [regex]::Replace($source, $pattern, $stub).Replace('UnpacmanWindowsRestart', $typeName)
        if ($source -match 'extern uint InitiateShutdown') { throw 'Native shutdown declaration remains in the test' }
        Add-Type -TypeDefinition $source -ErrorAction Stop
        $probe = $typeName -as [type]
        $privilegesBefore = (whoami.exe /priv | Out-String)
        $probe.GetMethod('RequestRestartWithUpdates').Invoke($null, @()) | Should Be 0
        $probe.GetField('LastMachine').GetValue($null) | Should BeNullOrEmpty
        $probe.GetField('LastFlags').GetValue($null) | Should Be 0x44
        $probe.GetField('LastGracePeriod').GetValue($null) | Should Be 0
        $probe.GetField('LastReason').GetValue($null) | Should Be 2147614724
        $probe.GetField('Result').SetValue($null, [uint32]1191)
        $probe.GetMethod('RequestRestartWithUpdates').Invoke($null, @()) | Should Be 1191
        $probe.GetField('Calls').GetValue($null) | Should Be 2
        (whoami.exe /priv | Out-String) | Should Be $privilegesBefore
    }
}
