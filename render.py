"""DOCX -> PDF -> page images, and a page-level visual comparison.

LibreOffice headless does the conversion in a throwaway user profile; pypdfium2
rasterises the result. PNG is written here with zlib so the only third-party
dependencies stay lxml and pypdfium2.

What the comparison is for: a repair that changes one property should change the page
where the fault was and leave the rest alone. It is a *review signal*, not a verdict.
A legitimate repair reflows later text, so a large difference is something to look at,
never automatic failure. LibreOffice and Word also paginate differently, so these
images approximate Word rather than reproduce it; the release check is opening the
file in Word.

Isolation here is a unique profile directory and a timeout. The container, dropped
privileges, blocked outbound network and resource caps described in IDEATION.md
section 9 are NOT implemented, so this is for local development, not public uploads.
"""
import os
import shutil
import struct
import subprocess
import sys
import tempfile
import time
import zlib
from pathlib import Path

import pypdfium2 as pdfium

TIMEOUT_SECONDS = 180
# On Windows soffice.exe is a launcher that can return before the PDF is flushed, so a
# conversion is only called a failure after the output has been given this long to
# appear and stop growing. Named so a failure test can shorten it instead of waiting
# the full window out; the production value is unchanged.
SETTLE_SECONDS = 30
REVIEW_SCALE = 2.0      # page images for a human to look at
COMPARE_SCALE = 0.4     # smaller raster for the difference metric
GREY_THRESHOLD = 16     # per-pixel tolerance, to ignore antialiasing noise

CANDIDATE_PATHS = (
    # A copy unpacked beside the project (msiexec /a needs no administrator rights),
    # so the workflow runs on a machine where LibreOffice cannot be installed properly.
    str(Path(__file__).resolve().parent / '.tools' / 'lo' / 'program' / 'soffice.exe'),
    r'C:\Program Files\LibreOffice\program\soffice.exe',
    r'C:\Program Files (x86)\LibreOffice\program\soffice.exe',
    '/usr/bin/soffice',
    '/usr/local/bin/soffice',
    '/Applications/LibreOffice.app/Contents/MacOS/soffice',
)


class RendererUnavailable(RuntimeError):
    """No LibreOffice binary was found. Callers degrade instead of inventing images."""


class RenderFailed(RuntimeError):
    """An available renderer could not produce a usable PDF."""


def find_soffice():
    """Path to a LibreOffice binary, or None. SOFFICE_PATH overrides the search."""
    override = os.environ.get('SOFFICE_PATH')
    if override:
        return override if Path(override).exists() else None
    for name in ('soffice', 'libreoffice'):
        found = shutil.which(name)
        if found:
            return found
    return next((p for p in CANDIDATE_PATHS if Path(p).exists()), None)


def renderer_status():
    binary = find_soffice()
    return {'available': binary is not None, 'binary': binary,
            'note': 'Visual checks are skipped without LibreOffice; structural checks still run.'}


def docx_to_pdf(source, out_dir, timeout=TIMEOUT_SECONDS):
    """Convert one .docx to PDF in out_dir and return the PDF path.

    The source is passed as an argument vector, never through a shell, and the
    document is never read as a command.
    """
    binary = find_soffice()
    if binary is None:
        raise RendererUnavailable('LibreOffice not found; set SOFFICE_PATH to its soffice binary')
    source, out_dir = Path(source).resolve(), Path(out_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    # Convert into a fresh directory: a failed retry must not reuse an older PDF.
    # Publish the result only once this conversion has produced a readable PDF.
    with tempfile.TemporaryDirectory(prefix='wtd-render-', dir=out_dir) as staging, \
            tempfile.TemporaryDirectory(prefix='wtd-profile-') as profile:
        # A unique profile stops a shared one from locking, and keeps recovery data out.
        command = [binary,
                   '-env:UserInstallation=' + Path(profile).resolve().as_uri(),
                   '--headless', '--invisible', '--nologo', '--nodefault',
                   '--norestore', '--nolockcheck', '--nofirststartwizard',
                   '--convert-to', 'pdf:writer_pdf_Export',
                   '--outdir', staging, str(source)]
        try:
            done = subprocess.run(command, capture_output=True, timeout=timeout,
                                  stdin=subprocess.DEVNULL, check=False)
        except subprocess.TimeoutExpired as error:
            raise RenderFailed('Conversion exceeded %ss and was stopped' % timeout) from error
        except OSError as error:
            raise RenderFailed('Could not launch LibreOffice: %s' % error) from error
        detail = (done.stderr or done.stdout or b'').decode('utf-8', 'replace').strip()[:400]
        if done.returncode != 0:
            raise RenderFailed('Conversion exited unsuccessfully (exit %s) %s'
                               % (done.returncode, detail))
        pdf = Path(staging) / (source.stem + '.pdf')
        deadline = time.monotonic() + SETTLE_SECONDS
        size, settled = -1, False
        while time.monotonic() < deadline:
            if pdf.is_file():
                current = pdf.stat().st_size
                if current > 0 and current == size:
                    settled = True
                    break
                size = current
            time.sleep(0.4)
        if not pdf.is_file():
            raise RenderFailed('No PDF produced (exit 0) %s' % detail)
        if pdf.stat().st_size == 0:
            raise RenderFailed('Empty PDF produced (exit 0) %s' % detail)
        if not settled:
            raise RenderFailed('PDF output did not settle within %ss' % SETTLE_SECONDS)
        try:
            with pdfium.PdfDocument(str(pdf)) as document:
                if len(document) == 0:
                    raise RenderFailed('PDF output contains no pages')
        except pdfium.PdfiumError as error:
            raise RenderFailed('Invalid PDF output: %s' % error) from error
        target = out_dir / pdf.name
        pdf.replace(target)
        return target


def _write_png(path, width, height, stride, channels, buffer):
    """Minimal 8-bit RGB PNG. Honours the bitmap's row stride."""
    raw = bytearray()
    for y in range(height):
        start = y * stride
        row = buffer[start:start + width * channels]
        raw.append(0)  # filter type 0
        if channels == 3:
            raw += row
        else:
            for x in range(width):  # drop alpha / padding bytes
                raw += row[x * channels:x * channels + 3]

    def chunk(kind, payload):
        return (struct.pack('>I', len(payload)) + kind + payload
                + struct.pack('>I', zlib.crc32(kind + payload) & 0xFFFFFFFF))

    header = struct.pack('>2I5B', width, height, 8, 2, 0, 0, 0)
    Path(path).write_bytes(b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', header)
                           + chunk(b'IDAT', zlib.compress(bytes(raw), 6))
                           + chunk(b'IEND', b''))
    return path


def _greyscale(bitmap):
    """Flat greyscale bytes for one rendered page."""
    width, height, stride = bitmap.width, bitmap.height, bitmap.stride
    channels, buffer = bitmap.n_channels, bytes(bitmap.buffer)
    grey = bytearray(width * height)
    for y in range(height):
        base = y * stride
        for x in range(width):
            p = base + x * channels
            grey[y * width + x] = (buffer[p] * 2 + buffer[p + 1] * 5 + buffer[p + 2]) // 8
    return grey, width, height


def pdf_pages(pdf, out_dir, prefix, scale=REVIEW_SCALE):
    """Write one PNG per PDF page; returns page records with sizes in points."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    pages, document = [], pdfium.PdfDocument(str(pdf))
    try:
        for index in range(len(document)):
            page = document[index]
            bitmap = page.render(scale=scale, rev_byteorder=True)
            image = out_dir / ('%s-page%02d.png' % (prefix, index + 1))
            _write_png(image, bitmap.width, bitmap.height, bitmap.stride,
                       bitmap.n_channels, bytes(bitmap.buffer))
            width, height = page.get_size()
            pages.append({'page': index + 1, 'image': str(image),
                          'width_pt': round(width, 1), 'height_pt': round(height, 1)})
    finally:
        document.close()
    return pages


def _difference_rasters(pdf, scale=COMPARE_SCALE):
    rasters, document = [], pdfium.PdfDocument(str(pdf))
    try:
        for index in range(len(document)):
            rasters.append(_greyscale(document[index].render(scale=scale, rev_byteorder=True)))
    finally:
        document.close()
    return rasters


def compare_pdfs(before_pdf, after_pdf, scale=COMPARE_SCALE, threshold=GREY_THRESHOLD):
    """Per-page changed-pixel ratios between two renderings.

    changed_ratio is the share of sampled pixels differing by more than `threshold`.
    Pages present in only one rendering are reported as added/removed rather than
    compared. No ratio is interpreted here as pass or fail.
    """
    before, after = _difference_rasters(before_pdf, scale), _difference_rasters(after_pdf, scale)
    comparisons = []
    for index in range(max(len(before), len(after))):
        if index >= len(before) or index >= len(after):
            comparisons.append({'page': index + 1,
                                'status': 'added' if index >= len(before) else 'removed'})
            continue
        (a, aw, ah), (b, bw, bh) = before[index], after[index]
        if (aw, ah) != (bw, bh):
            comparisons.append({'page': index + 1, 'status': 'page_size_changed',
                                'before_px': [aw, ah], 'after_px': [bw, bh]})
            continue
        changed = sum(1 for i in range(len(a)) if abs(a[i] - b[i]) > threshold)
        comparisons.append({'page': index + 1, 'status': 'compared',
                            'changed_ratio': round(changed / len(a), 5) if a else 0.0})
    return {'before_pages': len(before), 'after_pages': len(after),
            'page_count_delta': len(after) - len(before),
            'pages': comparisons,
            'note': 'Changed-pixel ratios are review signals. A repair legitimately '
                    'reflows later pages; a page-count change is not by itself a failure.'}


if __name__ == '__main__':
    if len(sys.argv) < 2:
        print(renderer_status())
    else:
        with tempfile.TemporaryDirectory() as tmp:
            pdf = docx_to_pdf(sys.argv[1], tmp)
            target = Path(sys.argv[2]) if len(sys.argv) > 2 else Path('render-out')
            for record in pdf_pages(pdf, target, Path(sys.argv[1]).stem):
                print(record)
