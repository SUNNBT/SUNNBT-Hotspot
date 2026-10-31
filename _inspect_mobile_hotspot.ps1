$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Runtime.WindowsRuntime

$networkInformation = [Windows.Networking.Connectivity.NetworkInformation, Windows.Networking.Connectivity, ContentType = WindowsRuntime]
$tetheringManagerType = [Windows.Networking.NetworkOperators.NetworkOperatorTetheringManager, Windows.Networking.NetworkOperators, ContentType = WindowsRuntime]
$profile = $networkInformation::GetInternetConnectionProfile()
if ($null -eq $profile) {
    throw 'Windows did not return an Internet connection profile.'
}

Write-Output "Internet profile: $($profile.ProfileName)"
$manager = $tetheringManagerType::CreateFromConnectionProfile($profile)
$configuration = $manager.GetCurrentAccessPointConfiguration()
Write-Output "Current SSID: $($configuration.Ssid)"
Write-Output "Tethering state: $($manager.TetheringOperationalState)"

$asyncAsTask = [System.WindowsRuntimeSystemExtensions].GetMethods() |
    Where-Object {
        $_.Name -eq 'AsTask' -and
        $_.IsGenericMethod -and
        $_.GetParameters().Count -eq 1 -and
        $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1'
    } |
    Select-Object -First 1
if ($null -eq $asyncAsTask) {
    throw 'Could not find the WinRT AsTask adapter.'
}

$resultType = [Windows.Networking.NetworkOperators.NetworkOperatorTetheringOperationResult, Windows.Networking.NetworkOperators, ContentType = WindowsRuntime]
function Wait-WinRT {
    param($Operation, [Type]$OperationResultType)
    $task = $asyncAsTask.MakeGenericMethod($OperationResultType).Invoke($null, (,$Operation))
    $task.GetAwaiter().GetResult()
}

$configuration.Ssid = 'SUNNBT111'
$configuration.Passphrase = 'SUNNBT111'
$configurationResult = Wait-WinRT $manager.ConfigureAccessPointAsync($configuration) $resultType
Write-Output "Configure result: $($configurationResult.Status); $($configurationResult.AdditionalErrorMessage)"

$startResult = Wait-WinRT $manager.StartTetheringAsync() $resultType
Write-Output "Start result: $($startResult.Status); $($startResult.AdditionalErrorMessage)"
Write-Output "Tethering state after start: $($manager.TetheringOperationalState)"
Write-Output "Configured SSID: $($manager.GetCurrentAccessPointConfiguration().Ssid)"
