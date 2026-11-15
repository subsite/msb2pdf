#!/usr/bin/env python3
"""Export a MobileSheets backup (.msb) to PDFs with annotations burned in.

Usage:
    python3 msb2pdf.py BACKUP.msb [OUTPUT_DIR] [--flat] [--yes]

    OUTPUT_DIR  defaults to "<backup name>_pdf" next to the backup.
    --flat      put all PDFs in one folder instead of one subfolder per Collection.
    -y, --yes   overwrite an existing OUTPUT_DIR without asking.

If OUTPUT_DIR already has files in it you are asked before it is replaced.
Its old contents are deleted only after the backup has been read successfully.

Songs that are in no collection go to "_No collection". A song that is in
several collections is written to each of those folders.

Requires PyMuPDF:  pip install pymupdf   (or in a venv / pipx)

Copyright (C) 2026 Fredrik Welander
Licensed under the GNU General Public License v3.0; see LICENSE.
"""
import os
import re
import shutil
import sqlite3
import struct
import sys
import tempfile

try:
    import pymupdf as fitz
except ImportError:
    try:
        import fitz
    except ImportError:
        sys.exit('PyMuPDF is required: pip install pymupdf')

FLT_MAX = 3.0e38
SQLITE_MAGIC = b'SQLite format 3\x00'
FONT_CANDIDATES = [
    '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf',
    '/usr/share/fonts/dejavu/DejaVuSans.ttf',
    '/Library/Fonts/Arial Unicode.ttf',
    '/System/Library/Fonts/Supplemental/Arial Unicode.ttf',
    'C:/Windows/Fonts/arial.ttf',
]
FONTFILE = next((p for p in FONT_CANDIDATES if os.path.exists(p)), None)


# ---------------------------------------------------------------- backup parsing

def find_db(f, total):
    """Locate the embedded SQLite DB; return (offset, size)."""
    chunk, pos, tail = 1 << 20, 0, b''
    while pos < total:
        f.seek(pos)
        buf = tail + f.read(chunk)
        i = buf.find(SQLITE_MAGIC)
        if i >= 0:
            off = pos - len(tail) + i
            f.seek(off - 8)
            size = struct.unpack('>q', f.read(8))[0]
            return off, size
        tail = buf[-16:]
        pos += chunk
    sys.exit('No database found in backup - is this a MobileSheets .msb file?')


def extract(msb, workdir):
    """Write ms.db and files/<FileId> into workdir. Returns db path."""
    total = os.path.getsize(msb)
    with open(msb, 'rb') as f:
        off, size = find_db(f, total)
        f.seek(off)
        dbpath = os.path.join(workdir, 'ms.db')
        with open(dbpath, 'wb') as out:
            out.write(f.read(size))
        c = sqlite3.connect(dbpath)
        files = {}
        for fid, sid, src in c.execute('select Id,SongId,Source from Files order by Id'):
            if src == 1:
                files.setdefault(sid, []).append(fid)
        c.close()
        filedir = os.path.join(workdir, 'files')
        os.makedirs(filedir)
        p = off + size
        f.seek(p)
        marker = f.read(8)
        while p + 16 <= total:
            f.seek(p)
            if f.read(8) != marker:
                break
            sid = struct.unpack('>q', f.read(8))[0]
            p += 16
            for fid in files.get(sid, []):
                f.seek(p)
                sz = struct.unpack('>q', f.read(8))[0]
                if sz < 0 or p + 8 + sz > total:
                    sys.exit(f'Corrupt file record at offset {p}')
                if sz:
                    with open(os.path.join(filedir, str(fid)), 'wb') as out:
                        remaining = sz
                        while remaining:
                            b = f.read(min(remaining, 1 << 24))
                            out.write(b)
                            remaining -= len(b)
                p += 8 + sz
    return dbpath, filedir


# ---------------------------------------------------------------- song building

def argb(v):
    v &= 0xFFFFFFFF
    return ((v >> 16) & 255) / 255, ((v >> 8) & 255) / 255, (v & 255) / 255, (v >> 24) / 255


def parse_order(s, n):
    pages = []
    for part in (s or '').split(','):
        part = part.strip()
        if not part:
            continue
        try:
            if '-' in part:
                a, b = part.split('-')
                pages += range(int(a) - 1, int(b))
            else:
                pages.append(int(part) - 1)
        except ValueError:
            pass
    return [p for p in pages if 0 <= p < n] or list(range(n))


def file_for(c, filedir, fid, path):
    raw = os.path.join(filedir, str(fid))
    if os.path.exists(raw):
        return raw
    # identical file shared between songs is stored only once
    for (i,) in c.execute('select Id from Files where Path=? and Path!="" order by Id', (path,)):
        cand = os.path.join(filedir, str(i))
        if os.path.exists(cand):
            return cand
    return None


def build_song(c, filedir, sid, title):
    out = fitz.open()
    for fid, path, order, ftype in c.execute(
            'select Id,Path,PageOrder,Type from Files where SongId=? order by Id', (sid,)):
        if ftype == 5:  # blank page
            out.new_page(width=595, height=842)
            continue
        raw = file_for(c, filedir, fid, path)
        if raw is None:
            print(f'  ! {title}: file missing from backup: {path}')
            continue
        try:
            if ftype == 1:
                src = fitz.open(raw, filetype='pdf')
                for p in parse_order(order, len(src)):
                    out.insert_pdf(src, from_page=p, to_page=p)
            elif ftype == 0:
                out.insert_pdf(fitz.open('pdf', fitz.open(raw).convert_to_pdf()))
            elif ftype == 2:
                lines = open(raw, 'rb').read().decode('utf-8', errors='replace').splitlines()
                for i in range(0, max(len(lines), 1), 70):
                    pg = out.new_page(width=595, height=842)
                    pg.insert_textbox(fitz.Rect(40, 40, 555, 802), '\n'.join(lines[i:i + 70]),
                                      fontsize=9, fontname='cour')
            else:
                print(f'  ! {title}: unsupported file type {ftype}: {path}')
        except Exception as e:
            print(f'  ! {title}: could not read {path}: {e}')
    return out if len(out) else None


def text_kwargs(text):
    try:
        text.encode('latin-1')
        return {'fontname': 'helv'}
    except UnicodeEncodeError:
        return {'fontname': 'uni', 'fontfile': FONTFILE} if FONTFILE else {'fontname': 'helv'}


def draw_note(pg, anchor, size, col, dm):
    """Quarter-note stamp drawn as vectors (no font needed).

    Geometry measured against MobileSheets' own rendering, in units of the
    stamp's Size (page units): the stored point is the top of the stem; the
    notehead centre is 0.165 right of and 0.88 below it.
    """
    import math
    sh = pg.new_shape()
    cx, cy = anchor.x + size * 0.165, anchor.y + size * 0.88
    rx, ry, tilt = size * 0.17, size * 0.11, math.radians(-20)
    ct, st = math.cos(tilt), math.sin(tilt)
    head = []
    for i in range(24):
        t = 2 * math.pi * i / 24
        x, y = rx * math.cos(t), ry * math.sin(t)
        head.append(fitz.Point(cx + x * ct - y * st, cy + x * st + y * ct) * dm)
    sh.draw_polyline(head + head[:1])
    sh.finish(fill=col, color=col, width=0, closePath=True)
    stem_x = anchor.x + size * 0.33
    sh.draw_line(fitz.Point(stem_x, cy - size * 0.02) * dm, fitz.Point(stem_x, anchor.y) * dm)
    sh.finish(color=col, width=max(size * 0.04, 0.3))
    sh.commit()


def draw_bezier_chain(shape, pts):
    """Newer MobileSheets strokes (header flag 0) are stored as fitted cubic
    Bezier chains: p0, (c1, c2, p1), (c1, c2, p2), ..."""
    if len(pts) < 4 or len(pts) % 3 != 1:
        draw_smooth(shape, pts)
        return
    for i in range(0, len(pts) - 3, 3):
        shape.draw_bezier(pts[i], pts[i + 1], pts[i + 2], pts[i + 3])


def draw_smooth(shape, pts):
    """Old strokes (header flag 1) are raw touch points. Draw them the way Android
    ink paths usually are: quadratic curves
    with each recorded point as control point and the midpoints between
    consecutive points as anchors."""
    if len(pts) < 3:
        shape.draw_polyline(pts if len(pts) > 1 else [pts[0], pts[0] + (0.01, 0)])
        return

    def quad(p0, c, p1):  # quadratic -> cubic Bezier
        shape.draw_bezier(p0, p0 + (c - p0) * (2 / 3), p1 + (c - p1) * (2 / 3), p1)

    cur = pts[0]
    for i in range(1, len(pts) - 1):
        mid = (pts[i] + pts[i + 1]) * 0.5
        quad(cur, pts[i], mid)
        cur = mid
    shape.draw_line(cur, pts[-1])


def draw_annotations(c, doc, sid):
    rows = c.execute('''select b.Id,b.Page,b.Type,b.Opacity,b.Zoom,b.ZoomY,b.Version,
        b.SourcePageWidth,b.SourcePageHeight,p.Count,p.Points
        from AnnotationsBase b left join AnnotationPoints p on p.AnnotationId=b.Id
        left join Layers l on l.SongId=b.SongId and l.Page=b.Page and l.LayerIndex=b.Layer
        where b.SongId=? and coalesce(l.Visible,1)=1
        order by b.Page,b.Layer,b.Id''', (sid,)).fetchall()
    n = 0
    for bid, page, typ, opac, z, zy, ver, sw, sh, cnt, pts in rows:
        if page is None or page >= len(doc) or not pts or not z:
            continue
        a = struct.unpack('<%dd' % (len(pts) // 8), pts)
        if len(a) < {0: 4, 3: 2, 6: 4}.get(typ, 5):
            continue
        pg = doc[page]
        W, H = pg.rect.width, pg.rect.height
        if ver == 0:  # old format: screen pixels at stored zoom
            sx, sy = 1 / z, 1 / (zy or z)
        else:         # newer: source-page units
            sx = W / sw if sw else 1
            sy = H / sh if sh else 1
        dm = pg.derotation_matrix

        def T(x, y):
            return fitz.Point(x * sx, y * sy) * dm

        alpha = (opac or 100) / 100
        try:
            if typ in (1, 2, 6):
                d = c.execute('select LineColor,LineWidth from DrawAnnotations where BaseId=?',
                              (bid,)).fetchone()
                if not d:
                    continue
                col = argb(d[0])
                width, coords = (d[1], a) if typ == 6 else (a[2], a[3:])
                strokes, cur = [], []
                for i in range(0, len(coords) - 1, 2):
                    x, y = coords[i], coords[i + 1]
                    if x > FLT_MAX or y > FLT_MAX:
                        if cur:
                            strokes.append(cur)
                        cur = []
                    else:
                        cur.append(T(x, y))
                if cur:
                    strokes.append(cur)
                if not strokes:
                    continue
                shape = pg.new_shape()
                for s in strokes:
                    if typ == 6:
                        shape.draw_polyline(s if len(s) > 1 else [s[0], s[0] + (0.01, 0)])
                    elif a[0] == 0:
                        draw_bezier_chain(shape, s)
                    else:
                        draw_smooth(shape, s)
                shape.finish(color=col[:3], width=max(width * sx, 0.3), closePath=False,
                             lineCap=1, lineJoin=1, stroke_opacity=alpha * (col[3] or 1))
                shape.commit()
            elif typ == 0:
                t = c.execute('select TextColor,Text,FontSize,FillColor,BorderColor,HasBorder,BorderWidth '
                              'from TextboxAnnotations where BaseId=?', (bid,)).fetchone()
                if not t or not t[1]:
                    continue
                r = fitz.Rect(T(a[0], a[1]), T(a[2], a[3])).normalize()
                fs = t[2] * sx
                fill = argb(t[3])
                shape = pg.new_shape()
                if fill[3] > 0:
                    shape.draw_rect(r)
                    shape.finish(fill=fill[:3], color=None, fill_opacity=fill[3])
                if t[5]:
                    shape.draw_rect(r)
                    shape.finish(color=argb(t[4])[:3], width=max(t[6] * sx, 0.3))
                shape.commit()
                box = fitz.Rect(r.x0, r.y0, r.x1 + fs * 2, r.y1 + fs)  # slack so text never drops
                pg.insert_textbox(box, t[1], fontsize=fs, color=argb(t[0])[:3],
                                  rotate=pg.rotation, **text_kwargs(t[1]))
            elif typ == 3:
                s = c.execute('select Size,FilePath,Color,Symbol from StampAnnotations where BaseId=?',
                              (bid,)).fetchone()
                if not s:
                    continue
                size = s[0] * sx * 1.5
                col = argb(s[2])[:3]
                ctr = fitz.Point(a[0] * sx, a[1] * sy)
                if 'note' in (s[1] or '') or s[3] == 'q':
                    draw_note(pg, ctr, s[0] * sx, col, dm)
                else:
                    sym = s[3] or '*'
                    pg.insert_text(ctr * dm + (-0.3 * size, 0.35 * size), sym, fontsize=size,
                                   color=col, rotate=pg.rotation, **text_kwargs(sym))
            else:
                continue
            n += 1
        except Exception as e:
            print(f'  ! annotation {bid} skipped: {e}')
    return n


# ---------------------------------------------------------------- main

def safe(name):
    name = re.sub(r'[\\/:*?"<>|\x00-\x1f]', '_', name or '').strip().rstrip('.')
    return name or 'untitled'


def unique(path):
    base, ext = os.path.splitext(path)
    k = 2
    while os.path.exists(path):
        path = f'{base} ({k}){ext}'
        k += 1
    return path


def confirm_overwrite(outdir, msb, assume_yes):
    """Return True if outdir should be cleared before writing."""
    if not os.path.isdir(outdir):
        if os.path.exists(outdir):
            sys.exit(f'{outdir} exists and is not a folder.')
        return False
    entries = os.listdir(outdir)
    if not entries:
        return False
    out_abs = os.path.realpath(outdir)
    msb_abs = os.path.realpath(msb)
    if msb_abs.startswith(out_abs + os.sep) or out_abs in (os.path.realpath(os.path.expanduser('~')), os.sep):
        sys.exit(f'Refusing to overwrite {outdir}: pick a dedicated output folder.')
    files = [os.path.join(r, f) for r, _, fs in os.walk(outdir) for f in fs]
    other = [f for f in files if not f.lower().endswith('.pdf')]
    if assume_yes:
        return True
    if not sys.stdin.isatty():
        sys.exit(f'Output folder {outdir} is not empty. Use --yes to overwrite it.')
    print(f'Output folder {outdir} already exists ({len(files)} files).')
    if other:
        print(f'  WARNING: {len(other)} of them are not PDFs, e.g. {os.path.relpath(other[0], outdir)}')
    ans = input('Delete its contents and write a fresh export? [y/N] ').strip().lower()
    if ans not in ('y', 'yes'):
        sys.exit('Aborted.')
    return True


def main():
    opts = {a for a in sys.argv[1:] if a.startswith('-')}
    args = [a for a in sys.argv[1:] if not a.startswith('-')]
    flat = '--flat' in opts
    unknown = opts - {'--flat', '-y', '--yes', '-h', '--help'}
    if unknown:
        sys.exit(f'Unknown option(s): {" ".join(sorted(unknown))}. See --help.')
    if not args or opts & {'-h', '--help'}:
        print(__doc__)
        sys.exit(0 if args else 1)
    msb = args[0]
    if not os.path.isfile(msb):
        sys.exit(f'Backup not found: {msb}')
    outdir = args[1] if len(args) > 1 else os.path.splitext(msb)[0] + '_pdf'
    clear = confirm_overwrite(outdir, msb, opts & {'-y', '--yes'})

    with tempfile.TemporaryDirectory() as work:
        print('Reading backup...')
        dbpath, filedir = extract(msb, work)
        if clear:
            shutil.rmtree(outdir)
        os.makedirs(outdir, exist_ok=True)
        c = sqlite3.connect(dbpath)
        colls = {}
        for sid, name in c.execute('select cs.SongId,c.Name from CollectionSong cs '
                                   'join Collections c on c.Id=cs.CollectionId'):
            colls.setdefault(sid, []).append(name)
        songs = c.execute('select Id,Title from Songs order by Title collate nocase').fetchall()
        n_pdf = n_ann = 0
        for i, (sid, title) in enumerate(songs, 1):
            print(f'[{i}/{len(songs)}] {title}')
            doc = build_song(c, filedir, sid, title)
            if doc is None:
                continue
            n_ann += draw_annotations(c, doc, sid)
            folders = ['.'] if flat else [safe(x) for x in colls.get(sid, ['_No collection'])]
            data = doc.tobytes(garbage=3, deflate=True)
            for folder in folders:
                d = os.path.join(outdir, folder)
                os.makedirs(d, exist_ok=True)
                with open(unique(os.path.join(d, safe(title) + '.pdf')), 'wb') as fh:
                    fh.write(data)
                n_pdf += 1
        c.close()
    print(f'\nDone: {n_pdf} PDFs, {n_ann} annotations -> {outdir}')


if __name__ == '__main__':
    main()
