# TECS Technical Compliance Studio

A desktop workflow for building lighting technical-compliance submissions from engineer-entered requirements or extracted PDF drawings. It compares specified and proposed products, maintains a reusable team product catalogue, and exports technical data sheets plus a commercial quotation.

## API key security

No OpenAI API key is included in this repository, its release installers, or the application defaults. Every user must open **API settings** in the application and enter their own key before product search can run.

The key is stored locally in that user's operating-system credential store. It is not written into project files, exports, source code, or the TECS repository. Users can replace or remove it at any time from **API settings**. Never add API keys to source files, `.env` files committed to Git, build scripts, screenshots, issues, or release assets.

## User workflow

1. Enter the project, client, consultant, contractor, and submission reference.
2. Start manually or extract a first draft from lighting PDF drawings.
3. Enter the basic lighting-legend details for every fitting type: type, quantity, mounting, wattage, lumens, CCT, CRI, IP/IK, UGR, and controls.
4. Choose from the approved manufacturers and browse the saved team catalogue using product-family, mounting, CCT, and control filters.
5. If the required product is missing or outdated, use the OpenAI Responses API to research that brand's official sources and compare up to five verified options.
6. Finalize one product, verify the live requirement comparison and deviation remarks, then enter an optional supplier currency and unit price.
7. Select the products for submission, choose the export folder, and create individual or combined Excel/PDF technical data sheets plus the commercial Excel quotation.

Excel compliance exports are generated from the supplied TECS technical-compliance workbook itself. Each selected product duplicates the native item page and preserves its logo, merged cells, five-column layout, fonts, borders, row heights, column widths, and print settings.

Commercial exports use the supplied TECS costing workbook, preserve its calculation structure, and populate the project details, selected products, currencies, supplier prices, quantities, and user-configured exchange rates. Export filenames use the project reference, project name, and client. The selected export folder is remembered locally until the user changes it.

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

- Push a version tag such as `v0.1.5` to build a normal NSIS `.exe` installer and attach it permanently to a GitHub Release.
- The `Windows installer` workflow can also be started manually for an internal test build; manual builds appear as temporary GitHub Actions artifacts.
- Run the `macOS application` workflow for a drag-and-drop `.dmg` containing the `.app`.

Public distribution should use a Windows code-signing certificate and an Apple Developer ID certificate with notarisation. Unsigned installers can still be tested internally but may display Windows SmartScreen or macOS Gatekeeper warnings.

### Windows installation from GitHub

1. Open the repository's **Releases** page.
2. Open the newest release and download the `.exe` listed under **Assets**.
3. Run the installer. Until the app is code-signed, Windows may show **Windows protected your PC**; use **More info** and **Run anyway** only when the installer came from the official TECS repository.
4. Open **API settings** and enter the user's own OpenAI API key. No OpenAI key is bundled with the application.

The shared Supabase catalogue contains reusable manufacturer product details only. It does not contain project names, customer files, commercial prices, exported documents, or OpenAI keys. Its publishable client key is intentionally distributable; never substitute a Supabase secret or `service_role` key in the application.

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
