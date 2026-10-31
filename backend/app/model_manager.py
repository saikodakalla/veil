"""Explicit, pinned model installation and strictly offline local inference.

Only install() can access the network. No document content reaches that path.
The model is an MIT-licensed, clinical token classifier, not a generative model.
"""
from __future__ import annotations

import hashlib
import json
import os
import threading
import time
import urllib.request
from pathlib import Path

os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")
os.environ.setdefault("DO_NOT_TRACK", "1")
os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")

MODEL_ID = "StanfordAIMI/stanford-deidentifier-v2"
# The Hugging Face safetensors bot's verified conversion of Stanford's v2
# revision 244fba67a64894533487fe4031232749c44f6216. We load no pickle weights.
MODEL_REVISION = "0e6286a5f456958cef18d89698a77ed5c694b0be"
MODEL_NAME = "Stanford Clinical Deidentifier"
MODEL_BYTES = 436_556_478
FILES = {
    "config.json": "769020a120d84409debd5c2d81bf5d0f6b98d249f16bbaad9089e95a1439fa6e",
    "special_tokens_map.json": "b6d346be366a7d1d48332dbc9fdf3bf8960b5d879522b7799ddba59e76237ee3",
    "tokenizer.json": "af642d812bf0d334d107f608506cf7dc90295481c9e037bfbd472426664996cc",
    "tokenizer_config.json": "7954e65e6bc0678520830ca4f8249f83014551470411f11bdd1d6a895ed90077",
    "vocab.txt": "79489a52be45e6fa033521e8ce8e4f62aedc0a742ee2aa6fc04667e5b0b1454d",
    "README.md": "964e25ba34c027e1e453ec94e17ea3423db4e11d200df515c62343dda3f28f6b",
    "model.safetensors": "872f108b34101535ab199f30b8864a896fc5d91d98e2599e077c2f93692eccfc",
}


class ModelUnavailable(RuntimeError):
    """The clinical model must be installed successfully before analysis."""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


class ModelManager:
    def __init__(self, model_dir: Path | str | None = None):
        data_root = Path(os.environ.get("REDACT_DATA_DIR", Path.home() / ".local/share/veil"))
        self.model_dir = Path(model_dir) if model_dir else data_root / "models/stanford-deidentifier-v2"
        self._state = "missing"
        self._progress = 0
        self._error: str | None = None
        self._pipeline = None
        self._device = "cpu"
        self._lock = threading.RLock()
        self.inference_lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._verified = False

    def _installed(self) -> bool:
        try:
            manifest = json.loads((self.model_dir / "installed.json").read_text())
            return (
                manifest.get("revision") == MODEL_REVISION
                and all((self.model_dir / name).is_file() for name in FILES)
            )
        except (OSError, ValueError):
            return False

    def status(self) -> dict:
        with self._lock:
            if self._state == "missing" and self._installed():
                self._state = "ready"
                self._progress = 100
            return {
                "state": self._state,
                "progress": self._progress,
                "model_id": MODEL_ID,
                "name": MODEL_NAME,
                "revision": MODEL_REVISION,
                "device": self._device,
                "download_bytes": MODEL_BYTES,
                "error": self._error,
                "license": "MIT",
                "language": "English",
                "loaded": self._pipeline is not None,
            }

    def start_install(self) -> dict:
        with self._lock:
            if self._thread and self._thread.is_alive():
                return self.status()
            if self._pipeline is not None:
                return self.status()
            self._state = "downloading"
            self._error = None
            self._progress = 0
            self._thread = threading.Thread(target=self.install, daemon=True, name="clinical-model-install")
            self._thread.start()
            return self.status()

    def install(self) -> dict:
        """Download only fixed model artifacts, verify every SHA-256, and warm up.

        Deliberately separate from get_pipeline(): offline inference never attempts
        a download, even if the local files are missing or corrupted.
        """
        with self._lock:
            self._state = "downloading"
            self._error = None
        try:
            self.model_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
            completed = 0
            for filename, expected in FILES.items():
                target = self.model_dir / filename
                if target.exists() and _sha256(target) == expected:
                    completed += target.stat().st_size
                    continue
                partial = target.with_suffix(target.suffix + ".part")
                request = urllib.request.Request(
                    f"https://huggingface.co/{MODEL_ID}/resolve/{MODEL_REVISION}/{filename}",
                    headers={"User-Agent": "Veil-Local-Redactor/1.0"},
                )
                digest = hashlib.sha256()
                with urllib.request.urlopen(request, timeout=60) as response:
                    with partial.open("wb") as stream:
                        os.chmod(partial, 0o600)
                        while chunk := response.read(1024 * 1024):
                            digest.update(chunk)
                            stream.write(chunk)
                            completed += len(chunk)
                            with self._lock:
                                self._progress = min(94, int(completed / MODEL_BYTES * 94))
                if digest.hexdigest() != expected:
                    partial.unlink(missing_ok=True)
                    raise RuntimeError("Downloaded model integrity check failed. Retry installation.")
                partial.replace(target)
            self._verified = True
            with self._lock:
                self._state = "loading"
                self._progress = 96
            self._load()
            manifest = {
                "model_id": MODEL_ID,
                "revision": MODEL_REVISION,
                "sha256": FILES,
                "installed_at": int(time.time()),
            }
            marker = self.model_dir / "installed.json"
            marker.write_text(json.dumps(manifest, indent=2))
            os.chmod(marker, 0o600)
            with self._lock:
                self._state = "ready"
                self._progress = 100
        except Exception as exc:
            with self._lock:
                self._state = "error"
                # Only installation/model errors can appear here, never input text.
                self._error = f"Model installation failed ({type(exc).__name__}). Check your connection and available disk space, then retry."
                self._pipeline = None
        return self.status()

    def _load(self):
        with self._lock:
            if self._pipeline is not None:
                return self._pipeline
            if not self._verified:
                for filename, checksum in FILES.items():
                    target = self.model_dir / filename
                    if not target.is_file() or _sha256(target) != checksum:
                        self._state = "error"
                        self._error = "Model files are incomplete or failed integrity verification. Reinstall the model."
                        raise ModelUnavailable(self._error)
                self._verified = True
            import torch
            from transformers import AutoModelForTokenClassification, AutoTokenizer, pipeline

            torch.set_num_threads(min(4, os.cpu_count() or 2))
            tokenizer = AutoTokenizer.from_pretrained(
                str(self.model_dir), local_files_only=True, use_fast=True,
            )
            tokenizer.model_max_length = 512
            model = AutoModelForTokenClassification.from_pretrained(
                str(self.model_dir), local_files_only=True, weights_only=True,
                use_safetensors=True,
            )
            model.eval()
            requested_device = os.environ.get("REDACT_DEVICE", "auto")
            self._device = "mps" if requested_device != "cpu" and torch.backends.mps.is_available() else "cpu"
            try:
                self._pipeline = pipeline(
                    "token-classification", model=model, tokenizer=tokenizer,
                    aggregation_strategy="simple", stride=96, device=self._device,
                )
                # Warm with synthetic text, never a user document.
                self._pipeline("Patient: Alex Example. MRN: 12345678.")
            except Exception:
                if self._device != "mps":
                    raise
                self._device = "cpu"
                self._pipeline = pipeline(
                    "token-classification", model=model.to("cpu"), tokenizer=tokenizer,
                    aggregation_strategy="simple", stride=96, device="cpu",
                )
                self._pipeline("Patient: Alex Example. MRN: 12345678.")
            return self._pipeline

    def get_pipeline(self):
        if not self._installed() and self._pipeline is None:
            raise ModelUnavailable("Install the clinical model before analyzing documents. Inference runs locally after installation.")
        try:
            return self._load()
        except ModelUnavailable:
            raise
        except Exception as exc:
            with self._lock:
                self._state = "error"
                self._error = "The local clinical model could not load. Reinstall the model or restart the app."
            raise ModelUnavailable(self._error) from exc


manager = ModelManager()
