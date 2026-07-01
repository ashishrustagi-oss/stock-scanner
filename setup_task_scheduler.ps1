$TaskName = "AshishCapital_TradeCycle"
$RepoPath = "C:\Users\HP\OneDrive\Desktop\Ashish\stock-scanner"
$BatchFile = "$RepoPath\run_trade.bat"
$LogDir = "$RepoPath\logs"

if (-not (Test-Path $LogDir)) {
    New-Item -ItemType Directory -Path $LogDir | Out-Null
    Write-Host "Created logs directory"
}

if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
    Write-Host "Removed existing task"
}

$Action = New-ScheduledTaskAction -Execute "cmd.exe" -Argument "/c `"$BatchFile`"" -WorkingDirectory $RepoPath

$Times = @("09:00","09:15","09:30","09:45","10:00","10:15","10:30","10:45","11:00","11:15","11:30","11:45","12:00","12:15","12:30","12:45","13:00","13:15","13:30","13:45","14:00","14:15","14:30","14:45","15:00","15:15","15:30")

$Triggers = @()
foreach ($T in $Times) {
    $Triggers += New-ScheduledTaskTrigger -Weekly -DaysOfWeek Monday,Tuesday,Wednesday,Thursday,Friday -At $T
}

$Settings = New-ScheduledTaskSettingsSet -ExecutionTimeLimit (New-TimeSpan -Minutes 10) -MultipleInstances IgnoreNew -DisallowStartIfOnBatteries $false -StopIfGoingOnBatteries $false -StartWhenAvailable $true

$Principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Highest

Register-ScheduledTask -TaskName $TaskName -Action $Action -Trigger $Triggers -Settings $Settings -Principal $Principal -Description "Ashish Capital trade cycle" | Out-Null

Write-Host ""
Write-Host "Task Scheduler setup COMPLETE" -ForegroundColor Green
Write-Host "Task name : $TaskName"
Write-Host "Runs      : Mon-Fri, every 15 min, 9:00 AM to 3:30 PM IST"
Write-Host "Log file  : $LogDir\trade_runner.log"
Write-Host ""
$Task = Get-ScheduledTask -TaskName $TaskName
Write-Host "Task state: $($Task.State)" -ForegroundColor Cyan
