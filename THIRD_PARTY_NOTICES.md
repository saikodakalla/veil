# Third-party notices

Veil application source is licensed under GNU Affero General Public License version 3 or later. The full text is in LICENSE. Dependencies retain their respective licenses; this file is a guide to the major components, not a replacement for their license texts.

- **PyMuPDF / MuPDF** — AGPL v3 or commercial license. [Official licensing information](https://pymupdf.readthedocs.io/en/latest/about.html#license-and-copyright). Veil's chosen application license is AGPL-3.0-or-later; no proprietary/commercial MuPDF license is included.
- **Stanford Clinical Deidentifier v2** — MIT, [model source and model card](https://huggingface.co/StanfordAIMI/stanford-deidentifier-v2). Downloaded separately, not included in this repository. Its pinned README travels with the model cache. The safetensors conversion preserves the original tensors and model license.
- **PyTorch** — BSD-style license, [source license](https://github.com/pytorch/pytorch/blob/main/LICENSE).
- **Transformers and Hugging Face Hub** — Apache 2.0, [Transformers license](https://github.com/huggingface/transformers/blob/main/LICENSE), [Hub license](https://github.com/huggingface/huggingface_hub/blob/main/LICENSE).
- **safetensors** — Apache 2.0, [license](https://github.com/huggingface/safetensors/blob/main/LICENSE).
- **React** — MIT, [license](https://github.com/facebook/react/blob/main/LICENSE).
- **Lucide icons** — ISC, [license](https://github.com/lucide-icons/lucide/blob/main/LICENSE).
- **FastAPI** — MIT, [license](https://github.com/fastapi/fastapi/blob/master/LICENSE).
- **Pillow** — HPND and bundled component notices, [license](https://github.com/python-pillow/Pillow/blob/main/LICENSE).
- **python-docx** — MIT, [license](https://github.com/python-openxml/python-docx/blob/master/LICENSE).
- **PyObjC** — MIT, [license](https://github.com/ronaldoussoren/pyobjc/blob/main/License.txt). Apple Vision is part of macOS and is not redistributed by Veil.
- **uv** — MIT or Apache 2.0, [source](https://github.com/astral-sh/uv). Installed separately by the launcher only when absent.

The distributed browser bundle includes React, React DOM, Scheduler, and Lucide. Their full license texts are copied to frontend/dist/licenses during each build.

Python/Node packages include additional transitive dependencies and their own license files. See backend/requirements.lock and frontend/package-lock.json for exact dependency versions.
