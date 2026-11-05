# PDF.js 6.3.289 — the paper viewer in the Deslopify reader

From the `pdfjs-dist` package, Apache-2.0 (see `LICENSE`), fetched from
cdn.jsdelivr.net on 2026-09-29. Vendored, like KaTeX, so the reader works with
no network and no Node.

- `pdf.min.mjs`, `pdf.worker.min.mjs` — the **legacy** build. The modern build
  calls `Map.prototype.getOrInsertComputed` and `Math.sumPrecise` with no
  fallback; they exist in current Safari and Chrome but not in last year's, and
  collaborators' machines are not all current. The legacy build is 5% larger.
- `cmaps/` — character maps for CID-keyed fonts.
- `standard_fonts/` — the fourteen standard fonts, for PDFs that do not embed them.
- `wasm/` — decoders for JBIG2 and JPEG 2000 images and for colour profiles
  (`jbig2.wasm`, `openjpeg.wasm`, `qcms_bg.wasm`) with their licences. The
  JavaScript fallbacks and the QuickJS form-script engine are left out.
- `iccs/` — the colour profile the decoders use.

To upgrade: replace these files from the same paths in a newer `pdfjs-dist`,
then check the viewer in Chrome (`deslopify/tools/look.py`) and in WebKit
(`deslopify/tools/webkit.swift`).
