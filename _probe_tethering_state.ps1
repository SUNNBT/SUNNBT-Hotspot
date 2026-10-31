$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Runtime.WindowsRuntime

$networkInformation = [Windows.Networking.Connectivity.NetworkInformation, Windows.Networking.Connectivity, ContentType = WindowsRuntime]
$tetheringManagerType = [Windows.Networking.NetworkOperators.NetworkOperatorTetheringManager, Windows.Networking.NetworkOperators, ContentType = WindowsRuntime]
$profile = $networkInformation::GetInternetConnectionProfile()
$manager = $tetheringManagerType::CreateFromConnectionProfile($profile)
$configuration = $manager.GetCurrentAccessPointConfiguration()

Write-Output "Tethering state: $($manager.TetheringOperationalState)"
Write-Output "SSID: $($configuration.Ssid)"
Write-Output "Password matches requested value: $($configuration.Passphrase -ceq 'SUNNBT111')"
