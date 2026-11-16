# 📦 Dedupe & Organize

A safe, cross-platform Python tool to **find and handle duplicate files** by content (SHA-256 hash), **keep the newest copy**, and optionally **organize the kept files** into tidy folders.

By default, it’s **safe**: runs in **dry-run mode** and makes **no changes** until you add `--apply`.

---

## ✨ Features
- Works with **any file type** and on **Linux, macOS, and Windows**
- **Keeps the newest copy** (by modification time; link mode prioritizes folder order)
- Safe actions: **Move**, **Trash/Recycle Bin**, **Delete**, or replace duplicates with **Links**
- Optional **organize** step for kept files (by Year/Month or Extension)
- **Multithreaded hashing** for speed
- **Progress bars** with [`tqdm`](https://pypi.org/project/tqdm/)
- **Hash cache** for fast re-runs
- Detailed **CSV/JSON reports** for auditing

---

## ⚠️ Safety Model
- **Dry-run by default**: No files are changed unless you add `--apply`.
- **Keep latest**: The most recent file is preserved in each duplicate set, except in link mode, which prioritizes folder order.
- **Reversible options**:
  - `--duplicate-action trash` → send duplicates to system Trash/Recycle Bin
  - `--duplicate-action move` → move duplicates to a folder
- **Permanent deletion** only if you explicitly choose `--duplicate-action delete`

---

## 📦 Requirements
- Python **3.8+**
- Optional (recommended):
  - [`tqdm`](https://pypi.org/project/tqdm/) → pretty progress bars
  - [`send2trash`](https://pypi.org/project/Send2Trash/) → safe Trash/Recycle Bin support

Install extras:
```bash
# macOS / Linux
python3 -m pip install --upgrade pip
python3 -m pip install tqdm send2trash

# Windows (PowerShell)
py -m pip install --upgrade pip
py -m pip install tqdm send2trash
```

---

## 🚀 Quick Start

### Safe preview (dry-run)
**Linux/macOS**
```bash
python3 dedupe_and_organize.py ~/Downloads
```
**Windows**
```powershell
py .\dedupe_and_organize.py "$HOME\Downloads"
```

---

### Apply changes safely

#### Send duplicates to Trash/Recycle Bin (recommended)
```bash
python3 dedupe_and_organize.py ~/Downloads --apply --duplicate-action trash
```

#### Move duplicates to a folder
```bash
python3 dedupe_and_organize.py ~/Downloads --apply --duplicate-action move --duplicates-dir ~/Duplicates
```

#### Permanently delete duplicates (**dangerous**)
```bash
python3 dedupe_and_organize.py ~/Downloads --apply --duplicate-action delete
```

#### Replace duplicates with links
```bash
python3 dedupe_and_organize.py ~/Primary ~/Backup --duplicates-action link --apply
python3 dedupe_and_organize.py ~/Primary ~/Backup --duplicates-action link --link-type symlink --apply
```
`--duplicates-action` and `--duplication-action` are aliases for `--duplicate-action`. `--link-type` defaults to
`hard`; `symlink` creates symbolic links instead. No extra modules are needed.

Link mode retains a matching file in the **first folder in `paths`**. If that
folder has no matching file, the first subsequent folder containing one is used.
The newest matching file within that folder is retained; all other copies are
replaced in place with links to it, except for existing links as described below.
No copy is added to folders without a match.
Preview without `--apply`.

`--keep-existing-links` is enabled by default in link mode. Existing symbolic
links and files with a hard-link count greater than one are left untouched,
even when they point to a different file. Detection uses Python's standard
`lstat()` API without following symbolic links. Hard links have no distinguished
"original": every name for a file with multiple hard links is treated as linked.
Use `--no-keep-existing-links` to allow replacing those entries too. These flags
do not change the move, delete, or trash actions.

Hard links require filesystem support and the same filesystem/volume. Symbolic
links can cross filesystems but may require extra permissions (for example,
Windows Developer Mode or administrator rights). If link creation fails, the
original duplicate remains unchanged, an error is printed, and the command exits
with a nonzero status; there is no automatic fallback to another link type.

Hard-linked files share content: editing one changes all linked copies.
Symbolic links depend on their target remaining available. If `--organize kept`
is also selected, the retained file is organized first and links point to its
final location instead of the original folder.

---

## 📂 Organize Kept Files

**By year/month**
```bash
python3 dedupe_and_organize.py ~/Photos --apply --organize kept --org-mode yyyymm --organize-root ~/Organized
```
Creates `~/Organized/YYYY/MM/...`

**By extension**
```bash
python3 dedupe_and_organize.py ~/Photos --apply --organize kept --org-mode ext --organize-root ~/Organized
```
Creates `~/Organized/_by_ext/jpg/`, `.../mp4/`, etc.

---

## ⚡ Performance
- Use `--workers N` to speed up hashing:
```bash
python3 dedupe_and_organize.py ~/BigFolder --workers 8
```
- Install `tqdm` for nice progress bars
- Large datasets benefit greatly from caching

---

## 💾 Hash Cache
Avoid re-hashing unchanged files.

- Default cache: `.dedupe_cache.json` in current folder
- Custom cache:
```bash
python3 dedupe_and_organize.py ~/Downloads --cache-file ~/.cache/dedupe_hashes.json
```
- Force re-hash:
```bash
python3 dedupe_and_organize.py ~/Downloads --refresh-cache
```
- Disable cache:
```bash
python3 dedupe_and_organize.py ~/Downloads --no-cache
```

---

## 📊 Reporting
Generate CSV/JSON reports:
```bash
python3 dedupe_and_organize.py ~/Downloads   --report-csv report.csv --report-json report.json
```

---

## 🛠 Command Reference
```
python3 dedupe_and_organize.py PATH [PATH2 PATH3 ...]
  --apply                         # perform actions; default is dry-run
  --duplicate-action {move,trash,delete,link}  # aliases: --duplicates-action, --duplication-action
  --link-type {hard,symlink}       # for link action; default: hard
  --keep-existing-links           # link mode: preserve existing links (default)
  --no-keep-existing-links        # link mode: allow replacing existing links
  --duplicates-dir DIR            # for move action
  --organize {none,kept}
  --org-mode {yyyymm,ext}
  --organize-root DIR
  --workers N                     # hashing threads (0=auto)
  --min-size BYTES
  --follow-symlinks
  --report-csv FILE
  --report-json FILE
  --cache-file FILE
  --no-cache
  --refresh-cache
  --no-color
```

---

## 🧩 How duplicates are detected
- Files are grouped by **size** and **SHA-256 hash**
- Filenames and locations are irrelevant
- The newest file by modification time is kept (link mode first prioritizes folder order)

---

## 🔄 Recovery & Rollback
- **Trash mode**: restore from system Trash/Recycle Bin
- **Move mode**: files are in `--duplicates-dir`
- **Delete mode**: irreversible — use with extreme caution

---

## 🛡 Best Practices
- Always start with a **dry-run**
- Prefer **Trash** or **Move** for your first runs
- Keep cache in a persistent location for large datasets
- Adjust `--workers` for speed on big datasets

---

## ❓ FAQ

**Q: Will this change file contents?**  
A: No. It moves, trashes, deletes, or replaces duplicates with links. Editing a
hard-linked file afterward changes the content seen through every hard link.

**Q: What if two files have the same content but different timestamps?**  
A: The newest (highest mtime) is kept, except in link mode, which prioritizes
folders in command-line order and keeps the newest match within the first one.

**Q: Does it follow symlinks?**  
A: Not by default; add `--follow-symlinks`.

**Q: Can I skip tiny files?**  
A: Yes, with `--min-size`.

---

## 📝 License
MIT License — free to use, modify, and share.
