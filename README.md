# photos-offload

Get disk space back from Apple Photos on macOS when iCloud Photos is set to
**Optimize Mac Storage** but Photos won't let go of the originals.

```sh
uvx git+https://github.com/JRombouts/apple-photos-disk-reclaim@v0.1.0
```

## Why

With "Optimize Mac Storage" on, Photos keeps full-resolution originals on disk
and only swaps them for smaller versions when macOS says it's running low on
space. Even then it frees only as much as the system asks for. On a disk with
plenty of room, a 200 GB library can stay at 200 GB indefinitely.

There's no button to say "offload now". This tool creates the low-space
condition on purpose and keeps it there until Photos has offloaded what it can.

## How it works

1. The tool writes filler files (zeros) to `~/photos_offload_filler` until
   only about 7 GB is free.
2. macOS sees the low space and asks Photos to purge originals that are already
   in iCloud.
3. Each time free space goes back up, the tool writes more filler, so the disk
   stays near that level and Photos keeps offloading in small steps.
4. Every few minutes it measures the library's `originals` folder. It stops when
   that folder reaches the target size (10 GB by default) or hasn't shrunk for an
   hour.
5. It then deletes all the filler. What remains is the space Photos released.

Safety nets while it runs:

- Free space is re-checked before every 64 MB written, so filler never pushes
  the disk below the reserve, even if something else is writing at the same time.
- If free space still drops below 3 GB, the tool deletes filler right away.
- Only one run can use the filler folder at a time.
- Filler is deleted first thing on exit, before anything else can fail. A second
  Ctrl-C or a closed terminal can't interrupt that.

## ⚠️ Read this first

**The tool fills your disk on purpose.** That's how it works, and it has some
consequences:

- **It only does something if iCloud Photos is on with "Optimize Mac Storage"
  selected** (Photos → Settings → iCloud). If it isn't, nothing gets offloaded
  and your disk stays nearly full until the one-hour stall timeout.
- **Let Photos finish uploading first.** Photos only offloads originals it has
  already uploaded to iCloud, so this shouldn't lose anything. Still, if a photo
  only exists on this Mac, keep a separate backup of it.
- **Other apps may fail to save files while it runs.** Don't do downloads,
  exports or builds at the same time, and expect the occasional "disk almost
  full" notification.
- **macOS may delete other purgeable data too.** That includes iCloud Drive files
  that are also in the cloud, caches, and local Time Machine snapshots.
  Downloaded files will be fetched again when you open them. Local snapshots are
  gone for good.
- **If the process is killed hard** (`kill -9`, crash, power loss, force quit of
  the terminal), the filler stays on disk. Delete the folder yourself:
  ```sh
  rm -rf ~/photos_offload_filler
  ```
  The next run also finds and removes leftover filler.
- **`--keep-filler` leaves your disk nearly full.** Only use it if you know why
  you want that.

The tool lists these warnings and asks for confirmation before it writes
anything. Pass `--yes` to skip the prompt.

## Requirements

- macOS, with iCloud Photos and "Optimize Mac Storage" turned on
- [uv](https://docs.astral.sh/uv/), or any Python 3.9+
- **Full Disk Access** for your terminal app (System Settings → Privacy &
  Security → Full Disk Access). Without it, the tool can't measure the size of
  the Photos library.

## Usage

Check how big the library is and how much space is free (read-only):

```sh
uvx git+https://github.com/JRombouts/apple-photos-disk-reclaim@v0.1.0 --report
```

Run it:

```sh
uvx git+https://github.com/JRombouts/apple-photos-disk-reclaim@v0.1.0
```

Keep Photos open, and keep the Mac plugged in and awake. The tool runs
`caffeinate` itself to stop idle sleep, but closing the lid still puts the Mac to
sleep. Press Ctrl-C at any time to stop. The filler is deleted on the way out.

From a checkout, `python3 photos_offload.py` does the same with nothing to
install.

### Options

| Option | Default | Description |
| --- | --- | --- |
| `--library PATH` | `~/Pictures/Photos Library.photoslibrary` | Photos library to watch |
| `--filler-dir PATH` | `~/photos_offload_filler` | Where filler goes; must be on the same volume as the library, owned by you and not writable by others |
| `--reserve-gb N` | `7` | Keep free space at about this level |
| `--critical-gb N` | `3` | Delete filler if free space falls below this (minimum 2) |
| `--target-gb N` | `10` | Stop once `originals` has shrunk to this size |
| `--stall-minutes N` | `60` | Stop if `originals` hasn't shrunk for this long |
| `--chunk-gb N` | `5` | Largest size of a single filler file |
| `--min-write-gb N` | `0.5` | Skip top-ups smaller than this |
| `--check-minutes N` | `5` | How often to measure the library |
| `--poll-seconds N` | `5` | How often to check free space |
| `--report` | | Print library size and free space once, then exit |
| `--watch` | | Print library size every `--check-minutes`; writes nothing |
| `-y`, `--yes` | | Skip the confirmation prompt |
| `--keep-filler` | | Don't delete the filler on exit (not recommended) |

All sizes are SI units: 1 GB = 10⁹ bytes.

## Tips

- `--watch` in a second terminal is a handy way to follow progress, or to see
  whether Photos keeps offloading after the tool has stopped.
- The space macOS shows as "purgeable" isn't counted as free here, so the
  numbers may differ from what Finder or About This Mac show.

## License

MIT. See [LICENSE](LICENSE).
