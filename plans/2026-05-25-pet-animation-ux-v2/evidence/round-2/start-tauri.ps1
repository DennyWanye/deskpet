$env:WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS = '--remote-debugging-port=9222'
Set-Location 'G:\projects\deskpet\tauri-app'
npm run tauri:dev *>&1 | Tee-Object -FilePath 'G:\projects\deskpet\plans\2026-05-25-pet-animation-ux-v2\evidence\round-2\tauri-dev.log'
