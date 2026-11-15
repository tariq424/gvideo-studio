# 1. Kill the stuck port 42021 (Fork)
Get-NetTCPConnection -LocalPort 42021 -State Listen -ErrorAction SilentlyContinue | Select-Object -ExpandProperty OwningProcess | ForEach-Object { taskkill /F /PID $_ }

# 2. Kill the stuck port 5005 (Visual Beat)
Get-NetTCPConnection -LocalPort 5005 -State Listen -ErrorAction SilentlyContinue | Select-Object -ExpandProperty OwningProcess | ForEach-Object { taskkill /F /PID $_ }

# 3. Start the Fork using Claude's new launcher
cd F:\code\video-studio-test
Start-Process -FilePath "powershell.exe" -ArgumentList "-NoProfile -ExecutionPolicy Bypass -File .\scripts\start-isolated-test.ps1" -WindowStyle Hidden

# 4. Start Visual Beat Studio
cd F:\code\visual-beat-studio
Start-Process -FilePath ".\.venv\Scripts\python.exe" -ArgumentList "app.py" -WindowStyle Hidden