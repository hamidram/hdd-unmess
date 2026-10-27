# HDD Unmess (Windows 11)

A small, standard-library-only Python console app to help investigate local disk usage.

## What it does
- Analyses a folder, one drive, or all detected fixed/removable drives.
- Reports drive capacity, used space and free space.
- Checks Windows Recycle Bin size when the Windows Shell API allows it.
- Finds large files (100 MB and above) and lists the largest 30.
- Estimates 10% and 25% of total drive capacity as space targets.
- Detects removable drives and lists other available volumes as potential destinations.
- Opens a selected folder/file in File Explorer.
- Can empty the Recycle Bin or delete eligible user TEMP files older than 7 days, after confirmation.
- Can move selected large files to another drive, after confirmation.

## Safety and limitations
- Scanning is read-only; it does not delete anything.
- Cleanup and move actions require explicit confirmation.
- The app skips inaccessible files and does not follow directory symlinks.
- The scan has a 200,000-file safety limit. Results may be incomplete on very large drives.
- Some USB drives report as fixed drives, so they may not be labelled "Removable".
- The 10%/25% targets are percentages of total drive capacity, not a promise that the recommended cleanup will achieve that amount.
- Moving a file removes it from its original location after the move succeeds. For important/irreplaceable files, copy to the external drive, verify the copy, then delete the original yourself.
- This is a space investigation assistant, not a duplicate finder or an uninstall manager.
- It does not inspect cloud-only file contents or accurately apportion Windows system/restore-point storage.
- Emptying the Recycle Bin is permanent. Check its contents first in Windows.

## Requirements
- Windows 11
- Python 3.10 or later
- VS Code (recommended)
- No third-party Python packages required

## Project files
- `app.py` — application
- `README.md` — instructions and safety notes
- `.gitignore` — ignores common Python/VS Code artifacts
