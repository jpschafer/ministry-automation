<#
Registers watch_upload.py as a Task Scheduler task that:
  - Starts when you log on (needs your logged-in session for the one-time browser auth)
  - Runs indefinitely in the background (no interval trigger -- it's a long-running watcher)
  - Restarts automatically if it crashes

Run this once, elevated (Run as Administrator), after adjusting the paths below.
#>

$TaskName    = "ChurchVideoUpload"
$ScriptPath  = "C:\Users\Jason Schafer\OneDrive\Development\Repositories\MinistryAutomation\youtube\upload-watcher\upload_watcher.py"
$PythonExe   = (Get-Command pythonw.exe).Source   # pythonw = no console window
$WorkingDir  = "C:\Users\Jason Schafer\Staging\ChurchVideos"

$Action  = New-ScheduledTaskAction -Execute $PythonExe -Argument "`"$ScriptPath`"" -WorkingDirectory $WorkingDir
$Trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$Settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -RestartCount 3 `
    -RestartInterval (New-TimeSpan -Minutes 1) `
    -ExecutionTimeLimit (New-TimeSpan -Days 0)   # 0 = no time limit; this task should run forever

Register-ScheduledTask -TaskName $TaskName -Action $Action -Trigger $Trigger -Settings $Settings `
    -Description "Watches for Colfax UMC / Levels UMC video files and uploads them to YouTube."

Write-Host "Task '$TaskName' registered. It will start at your next logon."
Write-Host "To run it right now without logging off: Start-ScheduledTask -TaskName '$TaskName'"
Write-Host "First run will open a browser for YouTube login -- keep an eye out for it."
