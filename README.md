# TECS Technical Compliance Studio

A private desktop workflow for building lighting technical-compliance submissions from engineer-entered requirements or extracted PDF drawings. It compares specified and proposed products and exports one technical data sheet per fitting in Excel or PDF.

## User workflow

1. Enter the project, client, consultant, contractor, and submission reference.
2. Start manually or extract a first draft from lighting PDF drawings.
3. Enter the basic lighting-legend details for every fitting type: type, quantity, mounting, wattage, lumens, CCT, CRI, IP/IK, UGR, and controls.
4. Choose from the 21 approved manufacturers and use the OpenAI Responses API to search only that brand's official domain.
5. Set the lumen and wattage tolerances, compare up to fifteen distinct official product options with their technical breakdown and quoted prices, finalize one, then verify the populated proposed values and drafted deviation remarks.
6. Select the products for submission and export individual or combined Excel/PDF technical data sheets.

Excel compliance exports are generated from the supplied TECS technical-compliance workbook itself. Each selected product duplicates the native item page and preserves its logo, merged cells, five-column layout, fonts, borders, row heights, column widths, and print settings.

Drawing extraction runs locally. Product search sends only normalized fixture criteria and the selected manufacturer domain to the OpenAI Responses API.

## Private local drawing AI

Lighting drawings are analysed by a local `llama.cpp` vision server using a Qwen2.5-VL GGUF model. The server listens only on `127.0.0.1`, uses a per-process secret, and is restricted to the TECS desktop origin. Page images, PDF text and prompts remain on the computer.

The vision model identifies lighting legend rows and interprets their meaning. The engine then re-reads each identified row from the PDF text layer and validates technical values deterministically. It preserves multi-option and ranged requirements such as `25/40 W`, `3000/4800 lm`, `3000/4000/6000 K`, voltage ranges, beam angles, LED density, waterproof status, mounting, dimensions, materials, optics, IP/IK, CRI, UGR, emergency duration and controls. It excludes switches, sockets, sensors and other non-luminaire electrical symbols.

When the user clicks **Approve & choose products**, the reviewed fixture list is stored in a private SQLite knowledge database. Any changed fields are recorded as before/after corrections. On later drawings from the same recognised or learned layout family, approved field corrections are automatically applied to the matching fixture symbol. Quantities explicitly printed in a legend or schedule are extracted; quantities absent from the schedule remain manual and are never invented by counting CAD symbols. Unfamiliar layouts become reusable local families after their first approval. The uploaded source PDFs and page previews stay in the operating system's local application-data folder and are never sent to product search.

- macOS: `~/Library/Application Support/TECS Lighting Quotation`
- Windows: `%LOCALAPPDATA%\TECS Lighting Quotation`

These approved corrections improve repeated extraction immediately and form a local labelled dataset for future detector improvements.

## How the AI pipeline works

1. **Local multimodal extraction:** a bundled `llama.cpp` runtime analyses rendered legend crops with a local Qwen vision model. PDF text, coordinates and optional OCR are also read on-device. The PDFs never leave the machine.
2. **Source-grounded validation:** the model identifies the row and interprets its layout; deterministic parsing verifies structured specifications against that exact legend row. Missing or uncertain values remain empty for review.
3. **Human verification and automatic improvement:** approval stores the original extraction and any corrected result. Field-level consensus is reused for the same local layout family and fixture symbol on later drawings. Project quantities remain manual.
4. **Cloud product research:** only anonymous technical criteria are sent to the OpenAI Responses API. Drawings, project names, customers, references and source-page images are never included. Web search is restricted to the selected manufacturer's official domain.
5. **Local compliance scoring:** the program recalculates matches using wattage/lumen options and tolerances plus checks for type, mounting, CCT ranges/options, voltage, beam angle, waterproof status, dimensions, materials, optics, CRI, IP, IK, UGR, emergency duration and controls. Non-official URLs are discarded.

The local model does not retrain itself after every correction. Corrections are applied safely as local field-level knowledge; the accumulated records can later train and evaluate a dedicated legend detector. This avoids uncontrolled model drift.

The first development launch downloads roughly 5 GB of model files into the local model cache. After that download, drawing analysis works offline. Release installers can bundle or install the model as part of a guided first-run setup.

## Desktop delivery

Both release builds package the local Python extraction/OCR service as a private sidecar. The user does not install Python, Node.js, OCR tools, or a database.

- Run the `Windows installer` GitHub Actions workflow for a normal NSIS `.exe` installer.
- Run the `macOS application` workflow for a drag-and-drop `.dmg` containing the `.app`.

Public distribution should use a Windows code-signing certificate and an Apple Developer ID certificate with notarisation. Unsigned installers can still be tested internally but may display Windows SmartScreen or macOS Gatekeeper warnings.

## Repository layout

- `apps/desktop`: React user interface and Tauri desktop shell.
- `services/local_engine`: local FastAPI service for PDF extraction, product search, secure API-key storage, and quotation export.
- `tests`: local extraction and matching tests.

## Development

### Local engine

```bash
cd services/local_engine
python -m venv .venv
source .venv/bin/activate
pip install -e .
tecs-engine
```

### Desktop interface

```bash
cd apps/desktop
pnpm install
pnpm dev
```

The interface expects the local engine at `http://127.0.0.1:8765`.
