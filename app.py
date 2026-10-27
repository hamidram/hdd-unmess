"""
HDD Unmess — Windows 11 disk-space assistant
Standard-library-only interactive console app.

Safety principles:
- Scanning is read-only.
- The app never automatically deletes personal files.
- Each cleanup/move action requires a separate confirmation.
- "Open folder" is available for every recommendation.
"""
from __future__ import annotations

import ctypes
import datetime as dt
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Iterable

APP_NAME = "HDD Unmess"
GIB = 1024 ** 3
TOP_FILES_LIMIT = 30
DEFAULT_SCAN_LIMIT = 200_000


def fmt_bytes(value: int | float) -> str:
    value = max(0, int(value))
    units = ("B", "KB", "MB", "GB", "TB")
    amount = float(value)
    for unit in units:
        if amount < 1024 or unit == units[-1]:
            return f"{amount:,.2f} {unit}"
        amount /= 1024
    return f"{amount:,.2f} TB"


def human_path(path: Path) -> str:
    return str(path)


def open_in_explorer(path: Path) -> None:
    """Open a folder, or select a file, in Windows File Explorer."""
    try:
        path = path.resolve()
        if path.is_file():
            subprocess.Popen(["explorer.exe", "/select,", str(path)])
        else:
            subprocess.Popen(["explorer.exe", str(path)])
    except Exception as exc:
        print(f"Could not open Explorer: {exc}")


def get_fixed_and_removable_drives() -> list[dict]:
    """Return Windows drive roots with type and available/total space."""
    if os.name != "nt":
        return []
    DRIVE_REMOVABLE = 2
    DRIVE_FIXED = 3
    get_type = ctypes.windll.kernel32.GetDriveTypeW
    drives = []
    for letter in "ABCDEFGHIJKLMNOPQRSTUVWXYZ":
        root = f"{letter}:\\"
        if not os.path.exists(root):
            continue
        try:
            kind = int(get_type(root))
            usage = shutil.disk_usage(root)
            if kind in (DRIVE_FIXED, DRIVE_REMOVABLE):
                drives.append({
                    "root": root,
                    "kind": "Removable" if kind == DRIVE_REMOVABLE else "Fixed",
                    "total": usage.total,
                    "used": usage.used,
                    "free": usage.free,
                })
        except (OSError, PermissionError):
            continue
    return drives


def query_recycle_bin() -> tuple[int | None, int | None]:
    """Use the Windows Shell API; returns (item_count, bytes), or (None, None)."""
    if os.name != "nt":
        return None, None

    class SHQUERYRBINFO(ctypes.Structure):
        _fields_ = [
            ("cbSize", ctypes.c_ulong),
            ("i64Size", ctypes.c_longlong),
            ("i64NumItems", ctypes.c_longlong),
        ]

    info = SHQUERYRBINFO()
    info.cbSize = ctypes.sizeof(info)
    try:
        result = ctypes.windll.shell32.SHQueryRecycleBinW(None, ctypes.byref(info))
        if result == 0:
            return int(info.i64NumItems), int(info.i64Size)
    except Exception:
        pass
    return None, None


def empty_recycle_bin() -> bool:
    """Empty all Windows recycle bins, without a second Windows confirmation."""
    if os.name != "nt":
        return False
    # SHERB_NOCONFIRMATION=0x1, SHERB_NOPROGRESSUI=0x2, SHERB_NOSOUND=0x4
    try:
        result = ctypes.windll.shell32.SHEmptyRecycleBinW(None, None, 0x1 | 0x2 | 0x4)
        return result == 0
    except Exception:
        return False


def get_user_temp_dirs() -> list[Path]:
    candidates = []
    for raw in (os.environ.get("TEMP"), os.environ.get("TMP"),
                str(Path.home() / "AppData" / "Local" / "Temp")):
        if raw:
            p = Path(raw)
            try:
                resolved = p.resolve()
                if resolved.exists() and resolved.is_dir() and resolved not in candidates:
                    candidates.append(resolved)
            except OSError:
                pass
    return candidates


def scan_temp_files(min_age_days: int = 7, max_files: int = DEFAULT_SCAN_LIMIT) -> tuple[list[Path], int, int]:
    """List user-temp files older than min_age_days. Does not delete anything."""
    cutoff = time.time() - min_age_days * 86400
    files: list[Path] = []
    total = 0
    seen = 0
    for root in get_user_temp_dirs():
        for current, dirs, names in os.walk(root, topdown=True, onerror=lambda _e: None):
            # Do not follow junctions/symlinks into other locations.
            dirs[:] = [d for d in dirs if not (Path(current) / d).is_symlink()]
            for name in names:
                seen += 1
                if seen > max_files:
                    return files, total, seen
                p = Path(current) / name
                try:
                    if p.is_symlink() or not p.is_file():
                        continue
                    stat = p.stat()
                    if stat.st_mtime <= cutoff:
                        files.append(p)
                        total += stat.st_size
                except (OSError, PermissionError):
                    continue
    return files, total, seen


def scan_large_files(root: Path, min_size: int = 100 * 1024 * 1024,
                     max_files: int = DEFAULT_SCAN_LIMIT) -> tuple[list[tuple[Path, int]], int, int]:
    """Scan files at least min_size bytes. Skips inaccessible paths and symlinks."""
    matches: list[tuple[Path, int]] = []
    scanned = 0
    skipped = 0
    for current, dirs, names in os.walk(root, topdown=True, onerror=lambda _e: None):
        dirs[:] = [d for d in dirs if not (Path(current) / d).is_symlink()]
        for name in names:
            scanned += 1
            if scanned > max_files:
                return sorted(matches, key=lambda x: x[1], reverse=True), scanned, skipped
            p = Path(current) / name
            try:
                if p.is_symlink() or not p.is_file():
                    continue
                size = p.stat().st_size
                if size >= min_size:
                    matches.append((p, size))
            except (OSError, PermissionError):
                skipped += 1
    matches.sort(key=lambda x: x[1], reverse=True)
    return matches, scanned, skipped


def ask_yes_no(prompt: str, default: bool = False) -> bool:
    suffix = " [Y/n] " if default else " [y/N] "
    answer = input(prompt + suffix).strip().lower()
    return default if not answer else answer in ("y", "yes")


def pause() -> None:
    input("\nPress Enter to continue...")


def print_drive_summary(drives: list[dict]) -> None:
    print("\nDrives found:")
    for i, d in enumerate(drives, 1):
        used_pct = (d["used"] / d["total"] * 100) if d["total"] else 0
        print(f" {i}. {d['root']}  ({d['kind']}) | "
              f"Free: {fmt_bytes(d['free'])} / {fmt_bytes(d['total'])} "
              f"| Used: {used_pct:.1f}%")


def choose_scan_target(drives: list[dict]) -> Path | None:
    print("\nChoose what to analyse:")
    print(" 1. A folder (enter a path)")
    print(" 2. A drive from the list")
    print(" 3. Scan all detected fixed/removable drives one by one")
    choice = input("Choice: ").strip()
    if choice == "1":
        raw = input("Folder path: ").strip().strip('"')
        p = Path(os.path.expandvars(os.path.expanduser(raw)))
        if p.exists() and p.is_dir():
            return p.resolve()
        print("That folder does not exist or is not a folder.")
        return None
    if choice == "2":
        if not drives:
            print("No drives found.")
            return None
        print_drive_summary(drives)
        raw = input("Enter drive number: ").strip()
        try:
            return Path(drives[int(raw) - 1]["root"])
        except (ValueError, IndexError):
            print("Invalid drive number.")
            return None
    if choice == "3":
        # A sentinel handled by the caller.
        return Path("__ALL_DRIVES__")
    print("Invalid choice.")
    return None


def scan_target(root: Path, drive_info: dict | None = None) -> dict:
    print(f"\nScanning {root} ...")
    print("This is read-only. It may take a while on a large drive.")
    matches, scanned, skipped = scan_large_files(root)
    try:
        usage = shutil.disk_usage(root.anchor or str(root))
        free = usage.free
        total = usage.total
    except OSError:
        free = total = 0
    return {
        "root": root, "matches": matches, "scanned": scanned,
        "skipped": skipped, "free": free, "total": total,
    }


def show_recommendations(result: dict, recycle: tuple[int | None, int | None],
                         temp_size: int, temp_files: list[Path],
                         drives: list[dict]) -> list[dict]:
    root = result["root"]
    total = result["total"]
    free = result["free"]
    used = max(0, total - free)
    recs: list[dict] = []

    print("\n" + "=" * 72)
    print(f"RESULTS FOR: {root}")
    print("=" * 72)
    if total:
        print(f"Total: {fmt_bytes(total)} | Used: {fmt_bytes(used)} | Free: {fmt_bytes(free)} "
              f"({free / total * 100:.1f}% free)")
    print(f"Files examined: {result['scanned']:,}; inaccessible items skipped: {result['skipped']:,}")
    if result["scanned"] > DEFAULT_SCAN_LIMIT:
        print("Scan safety limit reached; results may be incomplete.")

    count, recycle_bytes = recycle
    if count is not None and recycle_bytes is not None:
        print(f"Recycle Bin: {count:,} item(s), approximately {fmt_bytes(recycle_bytes)}")
        if recycle_bytes:
            recs.append({
                "title": f"Empty Recycle Bin ({fmt_bytes(recycle_bytes)} potentially recoverable)",
                "kind": "recycle", "bytes": recycle_bytes, "path": None,
                "detail": "Permanently removes items currently in all Windows Recycle Bins.",
            })
    else:
        print("Recycle Bin size could not be read (Windows Shell API unavailable).")

    if temp_size:
        recs.append({
            "title": f"Review old user temporary files ({fmt_bytes(temp_size)} across {len(temp_files):,} file(s))",
            "kind": "temp", "bytes": temp_size, "path": get_user_temp_dirs()[0] if get_user_temp_dirs() else Path.home(),
            "detail": "Only user TEMP/TMP files older than 7 days are considered; locked files are skipped.",
        })

    print("\nLargest files found (100 MB or larger):")
    if not result["matches"]:
        print("  No matching files found, or scan access was limited.")
    for i, (path, size) in enumerate(result["matches"][:TOP_FILES_LIMIT], 1):
        print(f" {i:2}. {fmt_bytes(size):>12}  {path}")

    if result["matches"]:
        largest = result["matches"][0]
        recs.append({
            "title": f"Review largest file ({fmt_bytes(largest[1])})",
            "kind": "open", "bytes": largest[1], "path": largest[0],
            "detail": "Opens File Explorer with the file selected; decide whether to archive or remove it.",
        })

    external = [d for d in drives if d["kind"] == "Removable" and d["root"].rstrip("\\").upper() != root.anchor.rstrip("\\").upper()]
    # Some USB drives report as Fixed; offer any other drive with enough free space as a secondary option.
    other_drives = [d for d in drives if d["root"].rstrip("\\").upper() != root.anchor.rstrip("\\").upper()]
    if other_drives:
        print("\nOther drives that may be used for storage:")
        for d in other_drives:
            print(f" - {d['root']} ({d['kind']}): {fmt_bytes(d['free'])} free")
    else:
        print("\nNo other drive detected. Connect an external drive and rescan to consider moving files.")

    # Percent targets refer to the scanned drive's total capacity, and are explicitly estimates.
    if total:
        for pct in (10, 25):
            target = int(total * pct / 100)
            recs.append({
                "title": f"Free {pct}% of drive capacity (target {fmt_bytes(target)})",
                "kind": "target", "bytes": target, "path": root,
                "detail": "Estimate based on total drive capacity, not the amount currently used. "
                          "Choose files to move to another drive or use cleanup recommendations.",
            })

    # Add a move-files recommendation if there are other volumes and large files.
    if other_drives and result["matches"]:
        best = max(other_drives, key=lambda d: d["free"])
        recs.append({
            "title": f"Review large files to move to {best['root']} ({fmt_bytes(best['free'])} free)",
            "kind": "move", "bytes": best["free"], "path": root,
            "detail": "Select individual large files; each move requires confirmation. Never move system folders.",
        })

    recs.append({
        "title": "Open scanned location in File Explorer",
        "kind": "open_folder", "bytes": 0, "path": root,
        "detail": "Browse the scanned folder yourself.",
    })

    print("\nRecommendations (enter a number to choose one):")
    for i, rec in enumerate(recs, 1):
        size = f" — {fmt_bytes(rec['bytes'])}" if rec["bytes"] else ""
        print(f" {i}. {rec['title']}{size}")
        print(f"    {rec['detail']}")
    print(" 0. Return to main menu")
    return recs


def clear_temp_files(files: list[Path]) -> None:
    if not files:
        print("No eligible temporary files were found.")
        return
    print(f"\nEligible files: {len(files):,}; total size: {fmt_bytes(sum_file_sizes(files))}")
    print("This action deletes only files in your user TEMP/TMP directories older than 7 days.")
    print("Files that are in use or inaccessible will be skipped.")
    if not ask_yes_no("Permanently delete these eligible temporary files?", False):
        print("Cancelled.")
        return
    deleted_bytes = 0
    deleted_count = 0
    errors = 0
    for p in files:
        try:
            size = p.stat().st_size
            p.unlink()
            deleted_bytes += size
            deleted_count += 1
        except (OSError, PermissionError):
            errors += 1
    print(f"Deleted {deleted_count:,} file(s), {fmt_bytes(deleted_bytes)}. Skipped/failed: {errors:,}.")


def sum_file_sizes(files: Iterable[Path]) -> int:
    total = 0
    for p in files:
        try:
            total += p.stat().st_size
        except OSError:
            pass
    return total


def move_selected_files(result: dict, drives: list[dict]) -> None:
    root = result["root"]
    other = [d for d in drives if d["root"].rstrip("\\").upper() != root.anchor.rstrip("\\").upper()]
    if not other:
        print("No other drive is available. Connect an external drive and rescan.")
        return
    print("\nChoose destination drive:")
    for i, d in enumerate(other, 1):
        print(f" {i}. {d['root']} ({d['kind']}) — {fmt_bytes(d['free'])} free")
    try:
        dest_drive = other[int(input("Destination number: ").strip()) - 1]
    except (ValueError, IndexError):
        print("Invalid selection.")
        return
    dest_root = Path(dest_drive["root"]) / "HDD-Unmess-Moved"
    print("\nChoose files from the largest-file list to move.")
    candidates = result["matches"][:TOP_FILES_LIMIT]
    for i, (p, size) in enumerate(candidates, 1):
        print(f" {i}. {fmt_bytes(size):>12}  {p}")
    raw = input("Enter file numbers separated by commas (or Enter to cancel): ").strip()
    if not raw:
        print("Cancelled.")
        return
    chosen = []
    try:
        indexes = sorted(set(int(x.strip()) for x in raw.split(",") if x.strip()))
        chosen = [candidates[i - 1] for i in indexes if 1 <= i <= len(candidates)]
    except ValueError:
        print("Invalid input.")
        return
    if not chosen:
        print("No valid files selected.")
        return
    required = sum(size for _, size in chosen)
    fresh_free = shutil.disk_usage(dest_drive["root"]).free
    if required > fresh_free:
        print(f"Not enough destination space. Selected: {fmt_bytes(required)}; free: {fmt_bytes(fresh_free)}.")
        return
    print("\nFiles selected:")
    for p, size in chosen:
        print(f" - {fmt_bytes(size)}  {p}")
    print(f"Destination folder: {dest_root}")
    print("Moving removes the original from its current location after a successful copy.")
    print("Do not move Windows, Program Files, AppData application folders, or unknown system files.")
    if not ask_yes_no("Proceed with these file moves?", False):
        print("Cancelled.")
        return
    dest_root.mkdir(parents=True, exist_ok=True)
    moved = 0
    failed = 0
    for source, size in chosen:
        try:
            # Preserve directory structure under a source-root-relative path when possible.
            try:
                rel = source.resolve().relative_to(root.resolve())
            except ValueError:
                rel = Path(source.name)
            destination = dest_root / rel
            destination.parent.mkdir(parents=True, exist_ok=True)
            if destination.exists():
                stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
                destination = destination.with_name(f"{destination.stem}-{stamp}{destination.suffix}")
            shutil.move(str(source), str(destination))
            moved += size
            print(f"Moved: {source} -> {destination}")
        except Exception as exc:
            failed += 1
            print(f"Could not move {source}: {exc}")
    print(f"Move finished. Moved: {fmt_bytes(moved)}; failed: {failed}.")


def handle_recommendation(rec: dict, result: dict, temp_files: list[Path],
                          drives: list[dict]) -> None:
    kind = rec["kind"]
    if kind == "open":
        open_in_explorer(rec["path"])
    elif kind == "open_folder":
        open_in_explorer(rec["path"])
    elif kind == "recycle":
        print("WARNING: Emptying the Recycle Bin is permanent.")
        if ask_yes_no("Empty all Windows Recycle Bins now?", False):
            if empty_recycle_bin():
                print("Recycle Bin emptied.")
            else:
                print("Windows reported an error. Try emptying it manually in File Explorer.")
    elif kind == "temp":
        print("Opening the user temporary folder first so you can review it.")
        if rec["path"]:
            open_in_explorer(rec["path"])
        if ask_yes_no("Would you like the app to delete eligible files older than 7 days?", False):
            clear_temp_files(temp_files)
    elif kind == "target":
        target = rec["bytes"]
        print(f"\nTarget: {fmt_bytes(target)}. This is {10 if '10%' in rec['title'] else 25}% of total drive capacity.")
        print("Safer ways to work toward the target:")
        print(" - Empty the Recycle Bin only if you do not need its contents.")
        print(" - Review large personal videos, archives, installers and disk images.")
        print(" - Move selected personal files to a verified external drive.")
        print(" - Use Windows Settings > System > Storage for Storage Sense and temporary-file review.")
        if ask_yes_no("Open the scanned folder to review files?", True):
            open_in_explorer(result["root"])
    elif kind == "move":
        move_selected_files(result, drives)


def print_windows_storage_guidance() -> None:
    print("\nWindows 11 built-in options:")
    print(" - Settings > System > Storage: review Temporary files and Storage Sense.")
    print(" - Settings > System > Storage > Cleanup recommendations (if shown).")
    print(" - Settings > System > Storage > Advanced storage settings > Disks & volumes.")
    print(" - Do not manually delete files from Windows, Program Files, ProgramData, or AppData.")
    print(" - Back up important files before moving them. Prefer copy-then-verify-then-delete for irreplaceable data.")


def main() -> None:
    if os.name != "nt":
        print(f"{APP_NAME} is designed for Windows 11. Some functions require Windows APIs.")
    print("=" * 72)
    print(f"{APP_NAME} — local disk-space assistant")
    print("=" * 72)
    print("Read-only scans first. Deletion and file moves require confirmation.")
    print("This tool does not uninstall applications or change Windows system files.")
    while True:
        drives = get_fixed_and_removable_drives()
        print("\nMain menu")
        print(" 1. Analyse a folder, drive, or all detected drives")
        print(" 2. Show drive space and external/removable drives")
        print(" 3. Show Windows storage-cleanup guidance")
        print(" 0. Exit")
        choice = input("Choice: ").strip()
        if choice == "0":
            print("Goodbye.")
            return
        if choice == "2":
            print_drive_summary(drives)
            pause()
            continue
        if choice == "3":
            print_windows_storage_guidance()
            pause()
            continue
        if choice != "1":
            print("Please enter 0, 1, 2, or 3.")
            continue

        target = choose_scan_target(drives)
        if target is None:
            continue
        roots = [Path(d["root"]) for d in drives] if str(target) == "__ALL_DRIVES__" else [target]
        for root in roots:
            result = scan_target(root)
            recycle = query_recycle_bin()
            temp_files, temp_size, temp_scanned = scan_temp_files()
            if temp_scanned > DEFAULT_SCAN_LIMIT:
                print("Note: temporary-file scan reached its safety limit.")
            recs = show_recommendations(result, recycle, temp_size, temp_files, drives)
            while True:
                raw = input("\nRecommendation number (0 to return): ").strip()
                if raw == "0":
                    break
                try:
                    rec = recs[int(raw) - 1]
                except (ValueError, IndexError):
                    print("Invalid recommendation number.")
                    continue
                handle_recommendation(rec, result, temp_files, drives)
                if ask_yes_no("Return to recommendation list?", True):
                    continue
                break


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nCancelled by user.")
    except EOFError:
        print("\nInput ended; exiting.")
