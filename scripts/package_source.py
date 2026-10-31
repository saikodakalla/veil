"""Package only application source and its bundled interface, never local records."""
from pathlib import Path
import zipfile

ROOT = Path(__file__).resolve().parents[1]
DIRECTORIES = ["backend/app", "frontend/src", "frontend/dist", "frontend/public", "frontend/scripts", "scripts", "tests", "docs", ".github", "sample-documents"]
FILES = [".gitattributes", ".gitignore", "README.md", "LICENSE", "THIRD_PARTY_NOTICES.md", "PRODUCT.md", "DESIGN.md", "start.sh", "Start Veil.command", "backend/requirements.txt", "backend/requirements.lock", "frontend/package.json", "frontend/package-lock.json", "frontend/tsconfig.json", "frontend/vite.config.ts", "frontend/index.html"]
SAMPLE_FIXTURES = {"01-consultation.txt", "02-referral.txt", "01-consultation.pdf", "02-referral.pdf", "03-scanned-consultation.png", "04-scanned-consultation.pdf", "README.md", "expected-identifiers.json"}
SKIP = {"__pycache__", "node_modules", ".venv", ".pytest_cache"}


def main():
    candidates = [ROOT / file for file in FILES]
    for folder in DIRECTORIES:
        candidates.extend(path for path in (ROOT / folder).rglob("*") if path.is_file())
    source = ROOT / "veil-source.zip"
    with zipfile.ZipFile(source, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(set(candidates)):
            relative = path.relative_to(ROOT)
            if relative.parts[0] == "sample-documents" and path.name not in SAMPLE_FIXTURES:
                continue
            if any(part in SKIP for part in relative.parts) or (relative.parts[0] != "sample-documents" and path.suffix in {".pyc", ".pyo", ".tsbuildinfo", ".pdf", ".docx", ".safetensors", ".bin", ".zip"}):
                continue
            archive.write(path, Path("veil") / relative)
    print(f"Ready to share: {source} ({source.stat().st_size:,} bytes)")


if __name__ == "__main__":
    main()
