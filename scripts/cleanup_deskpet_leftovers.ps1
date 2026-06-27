# Surgical cleanup of leftover DeskPet dev processes that block a fresh build.
# Kills ONLY: deskpet.exe, cargo.exe, the `python main.py` backend, my stuck
# verify launcher, and whatever owns the DeskPet dev ports. Leaves all other
# node/editor processes alone (user has many unrelated ones).
$ErrorActionPreference = "SilentlyContinue"

function Kill-Pid($id, $why) {
    if ($id) {
        $p = Get-Process -Id $id -ErrorAction SilentlyContinue
        if ($p) { Write-Output "  kill pid=$id name=$($p.Name) :: $why"; Stop-Process -Id $id -Force }
    }
}

Write-Output "=== deskpet.exe (Tauri shells) ==="
Get-Process deskpet -ErrorAction SilentlyContinue | ForEach-Object { Kill-Pid $_.Id "tauri shell" }

Write-Output "=== cargo.exe (build-dir lock holders) ==="
Get-Process cargo -ErrorAction SilentlyContinue | ForEach-Object { Kill-Pid $_.Id "cargo build lock" }

Write-Output "=== python main.py backends ==="
Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
    Where-Object { $_.CommandLine -like '*main.py*' } |
    ForEach-Object { Kill-Pid $_.ProcessId "python main.py backend" }

Write-Output "=== my stuck verify launcher (powershell + npx tauri) ==="
Get-CimInstance Win32_Process |
    Where-Object { $_.CommandLine -like '*verify_backfill_gui_launch*' -or $_.CommandLine -like '*tauri*dev*' } |
    ForEach-Object { Kill-Pid $_.ProcessId "verify launcher / tauri dev" }

Write-Output "=== free DeskPet dev ports (8100/5173/8150/5190) ==="
foreach ($port in 8100,5173,8150,5190) {
    Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue |
        ForEach-Object { Kill-Pid $_.OwningProcess "owns port $port" }
}

Start-Sleep -Seconds 2
Write-Output "=== post-cleanup port state ==="
foreach ($port in 8100,5173,8150,5190) {
    $c = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue
    if ($c) { Write-Output "  $port STILL LISTENING pid=$($c.OwningProcess -join ',')" } else { Write-Output "  $port free" }
}
Write-Output "=== remaining deskpet/cargo procs ==="
(Get-Process deskpet,cargo -ErrorAction SilentlyContinue | Measure-Object).Count
