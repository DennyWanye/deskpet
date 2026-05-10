# busybox-w32

Single-file Win32 build of GNU busybox 鈥?Ron Yorston's port.

## Source
- Upstream:  https://frippery.org/busybox/
- Author:    Ron Yorston (rys@gnu.org)
- Variant:   busybox64.exe (64-bit)
- Downloaded: 2026-05-10

## License
GPLv2. The binary is redistributable when accompanied by a copy of the
license. Source code is available at the upstream link.

## Why we ship it
DeskPet's run_shell tool prefers Git Bash 鈫?bundled busybox 鈫?PowerShell
鈫?cmd. End users without Git installed still get a competent unix-like
shell so LLM-generated ls / grep / sed / awk / find / cat | grep
commands Just Work without code changes.

## How to refresh
Run pwsh -File scripts/download_busybox.ps1 to re-fetch the latest
release.
