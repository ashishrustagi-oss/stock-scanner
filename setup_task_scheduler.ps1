# ============================================================
# Ashish Capital — Task Scheduler Setup
# Run this ONCE as Administrator to configure automatic
# trade cycle execution every 15 minutes during market hours
#
# Usage (in PowerShell as Administrator):
#   Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
#   .\setup_task_scheduler.ps1
# ============================================================

$TaskName = "AshishCapital_TradeCycle"
$RepoPath = "C:\Users\HP\OneDrive\Desktop\Ashish\stock-scanner"
$BatchFile = "$RepoPath\run_trade.bat"
$LogDir = "$RepoPath\logs"

# Create logs directory if it doesn't exist
if (-not (Test-Path $LogDir)) {
    New-Item -ItemType Directory -Path $LogDir | Out-Null
    Write-Host "Created logs directory: $LogDir"
}

# Remove existing task if present
if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
    Write-Host "Removed existing task: $TaskName"
}

# Define the action — run the batch file
$Action = New-ScheduledTaskAction `
    -Execute "cmd.exe" `
    -Argument "/c `"$BatchFile`"" `
    -WorkingDirectory $RepoPath

# Define triggers — every 15 minutes from 9:00 AM to 3:30 PM
# We start at 9:00 AM so the 9:15 check inside the batch file
# handles the exact market open time
$Triggers = @()
$StartTimes = @(
    "09:00", "09:15", "09:30", "09:45",
    "10:00", "10:15", "10:30", "10:45",
    "11:00", "11:15", "11:30", "11:45",
    "12:00", "12:15", "12:30", "12:45",
    "13:00", "13:15", "13:30", "13:45",
    "14:00", "14:15", "14:30", "14:45",
    "15:00", "15:15", "15:30"
)

foreach ($Time in $StartTimes) {
    $Trigger = New-ScheduledTaskTrigger `
        -Weekly `
        -DaysOfWeek Monday, Tuesday, Wednesday, Thursday, Friday `
        -At $Time
    $Triggers += $Trigger
}

# Settings — run even if on battery, don't stop if idle
$Settings = New-ScheduledTaskSettingsSet `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 10) `
    -MultipleInstances IgnoreNew `
    -DisallowStartIfOnBatteries $false `
    -StopIfGoingOnBatteries $false `
    -StartWhenAvailable $true

# Principal — run as current user
$Principal = New-ScheduledTaskPrincipal `
    -UserId $env:USERNAME `
    -LogonType Interactive `
    -RunLevel Highest

# Register the task
Register-ScheduledTask `
    -TaskName $TaskName `
    -Action $Action `
    -Trigger $Triggers `
    -Settings $Settings `
    -Principal $Principal `
    -Description "Ashish Capital stock scanner trade cycle — runs every 15 min during NSE market hours (Mon-Fri 9:15-15:30 IST)" | Out-Null

Write-Host ""
Write-Host "========================================" -ForegroundColor Green
Write-Host "  Task Scheduler setup COMPLETE" -ForegroundColor Green
Write-Host "========================================" -ForegroundColor Green
Write-Host ""
Write-Host "Task name    : $TaskName"
Write-Host "Runs         : Mon-Fri, every 15 min, 9:00 AM - 3:30 PM IST"
Write-Host "Batch file   : $BatchFile"
Write-Host "Log file     : $LogDir\trade_runner.log"
Write-Host ""
Write-Host "NEXT STEPS:" -ForegroundColor Yellow
Write-Host "1. Create your credentials file:"
Write-Host "   copy .env.local.template .env.local"
Write-Host "   (then edit .env.local with your real credentials)"
Write-Host ""
Write-Host "2. Test manually right now:"
Write-Host "   .\run_trade.bat"
Write-Host ""
Write-Host "3. The task will run automatically from tomorrow morning."
Write-Host ""

# Verify the task was created
$Task = Get-ScheduledTask -TaskName $TaskName
Write-Host "Task status  : $($Task.State)" -ForegroundColor Cyan
