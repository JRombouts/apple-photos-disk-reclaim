"""Coax Photos into offloading originals by holding the disk at its purge floor.

macOS only asks Photos to offload originals when free space runs low, and then
only frees as much as is needed. This script tops up filler files whenever free
space rises above a reserve, so the disk stays pinned at that level and Photos
keeps offloading a little at a time. When the originals folder reaches a target size
or stops shrinking, all filler is deleted and the reclaimed space is free.

Usage:
    uvx git+https://github.com/JRombouts/apple-photos-disk-reclaim
    python3 photos_offload.py            # equivalent, from a checkout

Press Ctrl-C at any time; the filler files are removed on exit. Sizes are in
SI units (1 GB = 10^9 bytes).
"""

from __future__ import annotations

import argparse
import os
import shutil
import signal
import subprocess
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

GB = 1_000_000_000
BLOCK = 64 * 1024 * 1024
ZEROS = bytes(BLOCK)


def log(message: str) -> None:
    """Prints a timestamped message.

    Args:
        message: Text to print.
    """
    print(f"{datetime.now():%H:%M:%S}  {message}", flush=True)


def gb(n_bytes: int) -> str:
    """Formats a byte count as SI gigabytes.

    Args:
        n_bytes: Number of bytes.

    Returns:
        The size formatted like ``"12.3 GB"``.
    """
    return f"{n_bytes / GB:.1f} GB"


def free_bytes(path: Path) -> int:
    """Returns the free space available to the user on the volume of a path.

    Args:
        path: Any path on the volume of interest.

    Returns:
        Free space in bytes, excluding space macOS reports as purgeable.
    """
    return shutil.disk_usage(path).free


@dataclass(frozen=True)
class LibrarySize:
    """On-disk sizes of a Photos library.

    Attributes:
        total: Size of the whole library package in bytes.
        originals: Size of its ``originals`` folder in bytes.
    """

    total: int
    originals: int

    def describe(self) -> str:
        """Returns a short human-readable summary of the sizes."""
        return f"Library: {gb(self.total)} (originals {gb(self.originals)})"


def library_size(library: Path) -> LibrarySize:
    """Measures a Photos library and its originals folder with one ``du`` pass.

    Args:
        library: Path to the ``.photoslibrary`` package.

    Returns:
        The library's total and originals sizes.

    Raises:
        RuntimeError: If ``du`` produced no usable output, e.g. because
            Terminal lacks Full Disk Access.
    """
    result = subprocess.run(
        ["du", "-k", "-d", "1", str(library)],
        capture_output=True, text=True, check=False,
    )
    sizes: dict[Path, int] = {}
    for line in result.stdout.splitlines():
        kib, _, path = line.partition("\t")
        if kib.isdigit():
            sizes[Path(path)] = int(kib) * 1024
    if library not in sizes or "Operation not permitted" in result.stderr:
        raise RuntimeError(
            f"Could not measure {library}: {result.stderr.strip()}\n"
            "Grant your terminal app Full Disk Access in System Settings > "
            "Privacy & Security > Full Disk Access, then restart it."
        )
    return LibrarySize(
        total=sizes[library], originals=sizes.get(library / "originals", 0)
    )


@dataclass
class Filler:
    """Manages the filler files that hold the disk at its purge floor.

    Attributes:
        directory: Directory where filler files are written.
        files: Filler files written so far, oldest first.
    """

    directory: Path
    files: list[Path] = field(default_factory=list)

    def total_bytes(self) -> int:
        """Returns the combined size of all existing filler files."""
        return sum(f.stat().st_size for f in self.files if f.exists())

    def add(self, size: int) -> int:
        """Writes a new filler file.

        Args:
            size: Number of bytes to write.

        Returns:
            Number of bytes actually written (less than ``size`` if the disk
            filled up first).
        """
        path = self.directory / f"filler_{len(self.files):05d}.bin"
        self.files.append(path)
        written = 0
        try:
            with path.open("wb") as handle:
                while written < size:
                    n = min(BLOCK, size - written)
                    handle.write(memoryview(ZEROS)[:n])
                    written += n
                handle.flush()
        except OSError as error:
            log(f"Write stopped early ({error}); continuing.")
        return written

    def remove_newest(self) -> None:
        """Deletes the most recently written filler file, if any."""
        if self.files:
            self.files.pop().unlink(missing_ok=True)

    def remove_all(self) -> None:
        """Deletes every filler file and the filler directory if empty."""
        while self.files:
            self.remove_newest()
        try:
            self.directory.rmdir()
        except OSError:
            pass


def parse_args() -> argparse.Namespace:
    """Parses command-line arguments.

    Returns:
        The parsed arguments.
    """
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument(
        "--library",
        default="~/Pictures/Photos Library.photoslibrary",
        help="Path to the Photos library.",
    )
    parser.add_argument(
        "--filler-dir",
        default="~/photos_offload_filler",
        help="Where to write filler files (must be on the same volume).",
    )
    parser.add_argument("--chunk-gb", type=float, default=5.0,
                        help="Maximum size of each filler file.")
    parser.add_argument("--reserve-gb", type=float, default=7.0,
                        help="Top filler up until free space is down to this.")
    parser.add_argument("--min-write-gb", type=float, default=0.5,
                        help="Skip top-ups smaller than this.")
    parser.add_argument("--critical-gb", type=float, default=3.0,
                        help="Delete filler if free space drops below this.")
    parser.add_argument("--target-gb", type=float, default=10.0,
                        help="Stop when originals shrink to this size.")
    parser.add_argument("--stall-minutes", type=float, default=60.0,
                        help="Stop if originals have not shrunk for this long.")
    parser.add_argument("--check-minutes", type=float, default=5.0,
                        help="How often to measure the originals folder.")
    parser.add_argument("--poll-seconds", type=float, default=15.0,
                        help="How often to check free space.")
    parser.add_argument("--report", action="store_true",
                        help="Print the library size and free space once.")
    parser.add_argument("--watch", action="store_true",
                        help="Print the library size every --check-minutes "
                             "until Ctrl-C, without writing any filler.")
    parser.add_argument("--keep-filler", action="store_true",
                        help="Do not delete filler on exit (not recommended: "
                             "leaves your disk nearly full).")
    parser.add_argument("-y", "--yes", action="store_true",
                        help="Skip the confirmation prompt.")
    return parser.parse_args()


def confirm(library: Path, filler_dir: Path, reserve: int) -> bool:
    """Explains the risks of filling the disk and asks the user to proceed.

    Args:
        library: Path to the ``.photoslibrary`` package.
        filler_dir: Directory where filler files will be written.
        reserve: Free space, in bytes, the disk will be held at.

    Returns:
        True if the user agreed to continue.
    """
    print(f"""
WARNING: this deliberately fills your disk until only ~{gb(reserve)} is free.

  * It only helps if iCloud Photos is on with "Optimize Mac Storage" selected
    (Photos > Settings > iCloud). Otherwise nothing is offloaded and the disk
    just sits nearly full until the stall timeout.
  * Let Photos finish uploading first. Photos only offloads originals that are
    already in iCloud, but have a backup of anything you cannot lose.
  * While it runs, other apps may fail to save files, and macOS may also evict
    other purgeable data (iCloud Drive files, caches, local Time Machine
    snapshots). Avoid heavy work on this Mac until it finishes.
  * Filler files go in {filler_dir} and are removed on exit or Ctrl-C. If the
    process is killed hard (kill -9, crash, power loss), delete that folder
    yourself to get the space back.

Library: {library}
""", flush=True)
    if not sys.stdin.isatty():
        log("No terminal to confirm on; pass --yes to run non-interactively.")
        return False
    try:
        answer = input("Continue? [y/N] ")
    except EOFError:
        return False
    return answer.strip().lower() in ("y", "yes")


def same_volume(a: Path, b: Path) -> bool:
    """Returns whether two existing paths live on the same volume."""
    return a.stat().st_dev == b.stat().st_dev


def keep_awake() -> None:
    """Prevents idle sleep for as long as this process lives, via caffeinate."""
    try:
        subprocess.Popen(
            ["caffeinate", "-i", "-w", str(os.getpid())],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
    except OSError:
        log("caffeinate not available; the Mac may sleep and pause progress.")


def watch(library: Path, interval: float) -> int:
    """Prints the library size and free space repeatedly until interrupted.

    Args:
        library: Path to the ``.photoslibrary`` package.
        interval: Seconds between measurements.

    Returns:
        Process exit code.
    """
    start = library_size(library)
    log(f"{start.describe()}   Free: {gb(free_bytes(library))}")
    log("Watching; Ctrl-C to stop.")
    while True:
        time.sleep(interval)
        current = library_size(library)
        log(f"{current.describe()}   "
            f"Change: {gb(current.total - start.total)}   "
            f"Free: {gb(free_bytes(library))}")


def run(args: argparse.Namespace) -> int:
    """Runs the offload loop until done, stalled, or interrupted.

    Args:
        args: Parsed command-line arguments.

    Returns:
        Process exit code.
    """
    library = Path(args.library).expanduser()
    if not (library / "originals").is_dir():
        log(f"Originals folder not found in {library}")
        return 1

    if args.report:
        log(f"{library_size(library).describe()}   "
            f"Free: {gb(free_bytes(library))}")
        return 0

    if args.watch:
        return watch(library, args.check_minutes * 60)

    if args.reserve_gb <= args.critical_gb:
        log("--reserve-gb must be larger than --critical-gb.")
        return 1
    chunk = int(args.chunk_gb * GB)
    reserve = int(args.reserve_gb * GB)
    min_write = int(args.min_write_gb * GB)
    critical = int(args.critical_gb * GB)

    filler_dir = Path(args.filler_dir).expanduser()
    if not args.yes and not confirm(library, filler_dir, reserve):
        log("Aborted; nothing was written.")
        return 1

    filler_dir.mkdir(exist_ok=True)
    if not same_volume(filler_dir, library):
        log(f"{filler_dir} is not on the same volume as {library}; filling it "
            "would not make Photos offload anything. Pick another --filler-dir.")
        Filler(filler_dir).remove_all()  # Removes the directory if empty.
        return 1

    leftovers = sorted(filler_dir.glob("filler_*.bin"))
    if leftovers:
        stale = sum(f.stat().st_size for f in leftovers)
        log(f"Removing {len(leftovers)} leftover filler files ({gb(stale)}) "
            "from a previous run.")
        for f in leftovers:
            f.unlink(missing_ok=True)
    filler = Filler(filler_dir)
    keep_awake()
    target = int(args.target_gb * GB)
    stall = args.stall_minutes * 60
    check_interval = args.check_minutes * 60

    start = library_size(library)
    max_filler = start.originals + 50 * GB
    best_size = start.originals
    best_time = time.monotonic()
    last_check = time.monotonic()

    log(f"{start.describe()}   Free: {gb(free_bytes(filler_dir))}")
    log(f"Holding free space near {gb(reserve)}; Ctrl-C to stop and clean up.")

    try:
        while True:
            free = free_bytes(filler_dir)

            if free < critical:
                filler.remove_newest()
                log(f"Free space critical ({gb(free)}); removed one filler file.")
                time.sleep(args.poll_seconds)
                continue

            top_up = min(chunk, free - reserve)
            if top_up >= min_write and filler.total_bytes() < max_filler:
                written = filler.add(top_up)
                log(f"Wrote {gb(written)} filler "
                    f"(total {gb(filler.total_bytes())}, "
                    f"free {gb(free_bytes(filler_dir))}).")
                continue

            now = time.monotonic()
            if now - last_check >= check_interval:
                last_check = now
                current = library_size(library)
                size = current.originals
                log(f"{current.describe()}   "
                    f"Freed: {gb(start.total - current.total)}   "
                    f"Filler: {gb(filler.total_bytes())}   Free: {gb(free)}")
                if size < best_size - GB:
                    best_size, best_time = size, now
                if size <= target:
                    log("Target reached.")
                    break
                if now - best_time >= stall:
                    log(f"No progress for {args.stall_minutes:.0f} min; stopping.")
                    break

            time.sleep(args.poll_seconds)
    finally:
        if args.keep_filler:
            log(f"WARNING: leaving {gb(filler.total_bytes())} of filler in "
                f"{filler_dir}; your disk stays nearly full until you delete "
                "it.")
        else:
            log("Removing filler files...")
            filler.remove_all()
            end = library_size(library)
            log(f"Done. Library: {gb(start.total)} -> {gb(end.total)}   "
                f"Originals: {gb(start.originals)} -> {gb(end.originals)}   "
                f"Free: {gb(free_bytes(filler_dir.parent))}")
    return 0


def main() -> None:
    """Entry point: makes termination signals clean up like Ctrl-C."""
    if sys.platform != "darwin":
        sys.exit("photos-offload only works on macOS.")
    for sig in (signal.SIGTERM, signal.SIGHUP):
        signal.signal(sig, lambda *_: sys.exit(1))
    try:
        sys.exit(run(parse_args()))
    except KeyboardInterrupt:
        sys.exit(130)
    except RuntimeError as error:
        sys.exit(str(error))


if __name__ == "__main__":
    main()
