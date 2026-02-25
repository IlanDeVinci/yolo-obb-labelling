# YOLO OBB Labelling

Desktop tool (PyQt6) for image annotation with:

- **OBB** (Oriented Bounding Boxes)
- **BBox** (Axis-aligned bounding boxes)
- Optional YOLO model inference (Ultralytics)
- Undo/redo-friendly editing workflow

## Features

- Draw and edit OBB/BBox labels
- Switch mode **OBB <-> BBox** with save/restore behavior per mode
- Explicit **Convert Labels to OBB/BBox** actions (destructive conversion, undoable)
- Multi-select and mass delete labels
- Model inference on current image or all images
- Team/project management with autosave

## Requirements

- Windows 10/11 (recommended)
- Python virtual environment (project uses `.venv`)
- Microsoft Visual C++ Redistributable 2015–2022 (x64)

## Setup

From project root:

```powershell
.\setup_venv.bat
```

Then run:

```powershell
.\run_app.bat
```

## Inference dependencies

Inference uses `ultralytics` + `torch` (CPU wheels by default in this project).

If inference is missing/broken in the project environment:

```powershell
python -m pip install -r requirements-inference.txt
```

For DLL-related torch issues on Windows:

```powershell
python -m pip install --upgrade --force-reinstall torch torchvision --index-url https://download.pytorch.org/whl/cpu
```

## Diagnostics (DLL / inference)

Inference runtime writes diagnostic logs here:

- `diagnostics/inference_dll.log`

If inference fails, share the latest lines from that file.

## Main shortcuts

### Modes & navigation

- `W` : Draw mode
- `S` : Select mode
- `Ctrl+B` : Toggle OBB/BBox mode
- `A` / `Left` : Previous image
- `D` / `Right` / `Space` : Next image

### Selection / editing

- `Ctrl+drag` (in Select mode): rubber-band multi-select
- `Ctrl+A` : Select all labels
- `Delete` / `Backspace` : Delete selected labels
- `Ctrl+Z` / `Ctrl+Y` : Undo / Redo

### Transform gestures (Select mode)

- Drag item: move label
- Drag corner handle: reshape
- `Alt` or `Ctrl` + drag corner/edge handle: uniform scale
- Drag yellow rotation handles: rotate (OBB)
- `Shift` + drag directly on OBB: quick rotate

### Model

- `Ctrl+R` : Run model on current image
- `Ctrl+Shift+R` : Run model on all images

## Project layout (high level)

- `main.py` : app entrypoint
- `app/ui/` : main window and dialogs
- `app/canvas/` : drawing/edit interactions
- `app/models/` : label, dataset, project state
- `app/inference/` : YOLO runtime + predictor
- `projets/` : local project data (images, labels, project JSON)

## Notes

- OBB and BBox labels are stored in separate folders (`labels/OBB` and `labels/BB`).
- Mode toggle can restore previously saved labels from the target mode.
- Conversion actions are explicit and undoable.
