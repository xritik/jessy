Add-Type -AssemblyName System.Runtime.WindowsRuntime

Function Await($WinRtTask, $ResultType) {
    $asTask = ([System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object {
        $_.Name -eq "AsTask" -and $_.GetParameters().Count -eq 1 -and
        $_.GetParameters()[0].ParameterType.Name -eq "IAsyncOperation``1"
    })[0]
    $asTaskAsync = $asTask.MakeGenericMethod($ResultType).Invoke($null, @($WinRtTask))
    $asTaskAsync.Wait(-1) | Out-Null
    $asTaskAsync.Result
}
[Windows.Devices.Radios.Radio,Windows.System.Devices,ContentType=WindowsRuntime] | Out-Null
[Windows.Devices.Radios.RadioAccessStatus,Windows.System.Devices,ContentType=WindowsRuntime] | Out-Null

$access = Await ([Windows.Devices.Radios.Radio]::RequestAccessAsync()) ([Windows.Devices.Radios.RadioAccessStatus])
Write-Host "Access status: $access"

$radios = Await ([Windows.Devices.Radios.Radio]::GetRadiosAsync()) ([System.Collections.Generic.IReadOnlyList[Windows.Devices.Radios.Radio]])
Write-Host "Radio count: $($radios.Count)"
$radios | ForEach-Object { Write-Host " - $($_.Name) [$($_.Kind)] State=$($_.State)" }
