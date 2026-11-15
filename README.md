# msb2pdf

Export a [MobileSheets](https://www.zubersoft.com/mobilesheets/) backup (`.msb`) to plain PDFs, with your hand-drawn annotations burned into the pages.

MobileSheets keeps annotations (pen strokes, highlights, text boxes, stamps) in its database, not in the PDFs, so exporting the score files alone loses them. This script reads the backup directly, rebuilds every song as a PDF and draws the annotations onto it.

## Requirements

- Python 3.8+
- [PyMuPDF](https://pymupdf.readthedocs.io/)

```sh
pip install pymupdf
# or, if your system Python is externally managed:
python3 -m venv ~/.venvs/msb && ~/.venvs/msb/bin/pip install pymupdf
```

## Usage

```sh
python3 msb2pdf.py BACKUP.msb [OUTPUT_DIR] [--flat]
```

| Argument | Meaning |
|---|---|
| `BACKUP.msb` | Backup made in MobileSheets (Settings → Backup). |
| `OUTPUT_DIR` | Where to write the PDFs. Defaults to `<backup name>_pdf` next to the backup. It must not exist yet or must be empty. |
| `--flat` | Put all PDFs in one folder instead of one subfolder per collection. |

Example:

```sh
python3 msb2pdf.py ~/Downloads/MobileSheets_2026-11-15.msb
```

## Output

- One PDF per song, named after the song title. Duplicate titles get ` (2)`, ` (3)`, … added.
- One subfolder per MobileSheets **Collection**. Songs in no collection go to `_No collection`. A song in several collections is copied into each folder.
- Songs made of several files are merged in MobileSheets' page order. Image files (JPG/PNG) become PDF pages, and text/ChordPro files are written as plain monospace text.
- Annotations are drawn as vector content in the page itself, so every PDF viewer shows them. They can't be edited as separate PDF annotations.
- Hidden annotation layers are skipped.

## Limitations

- **Stamps:** note stamps are drawn as a generic quarter note. Other symbol stamps are drawn as text. MobileSheets' built-in stamp images aren't in the backup.
- **Text boxes:** these use Helvetica, or a system Unicode font for non-Latin text, so the font differs from the app's. Size and position are kept.
- **Text/ChordPro songs:** these aren't formatted the way MobileSheets shows them.
- **Not exported:** setlists, audio files, MIDI, links and smart buttons.

## How it works

The `.msb` format is undocumented. This is what the script relies on, worked out from a backup created by MobileSheets in 2026:

1. **Settings:** named XML preference blocks (`2-byte name length, name, 8-byte size, data`), followed by a few more blocks. All of these are ignored.
2. **Database:** the SQLite database (`8-byte size` + `SQLite format 3…`). The script finds it by searching for the SQLite header.
3. **Song files:** a sequence of records, `8-byte marker, 8-byte song id`, then for each of that song's files (from the `Files` table, `Source = 1`, in id order) `8-byte size + bytes`. A file shared by several songs is stored once, and the other copies have size 0. The sequence ends with `-1`.

All integers are big-endian.

Annotation data in the database:

- **`AnnotationsBase`:** song, page, type (0 text, 1 pen, 2 highlighter, 3 stamp, 6 line), opacity (percent), zoom and version.
- **`AnnotationPoints.Points`:** little-endian doubles. Pen and highlighter: `[flag, n, width, x0, y0, x1, y1, …]`, with `(FLT_MAX, FLT_MAX)` separating sub-strokes. Line: `[x0, y0, x1, y1]`. Text box: `[x0, y0, x1, y1, …]`. Stamp: `[x, y]`.
- **Coordinates:**
  - Version 0: screen pixels at `Zoom`, so page point = value / Zoom.
  - Version 2/3: source-page units (`SourcePageWidth` × `SourcePageHeight`), scaled to the PDF page.
  - Crop settings don't affect annotation coordinates.
- **Stamp anchors:** the stored point is about 0.87 × stamp size above the notehead.
- **Rotated pages:** coordinates are in the visible (rotated) orientation.

## License

GPL-3.0. See [LICENSE](LICENSE).
