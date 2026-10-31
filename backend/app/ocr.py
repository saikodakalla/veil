"""Offline OCR using Apple's on-device Vision framework, with Tesseract fallback.

The normal Mac path uses an installed PyObjC wheel and needs no developer tools.
A bundled Swift helper is available as a fallback if that bridge is absent.
No document is sent to a service or included in diagnostic messages.
"""
from __future__ import annotations

import csv
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import tempfile
import threading


class OCRError(RuntimeError):
    pass


_COMPILE_LOCK = threading.Lock()
_SWIFT = r'''
import Foundation
import Vision
import ImageIO

struct Input: Decodable { let path: String }
struct Word: Encodable { let text: String; let box: [Double]; let confidence: Double; let separator: String }
struct Output: Encodable { let words: [Word] }

do {
    let data = FileHandle.standardInput.readDataToEndOfFile()
    let input = try JSONDecoder().decode(Input.self, from: data)
    let url = URL(fileURLWithPath: input.path)
    let request = VNRecognizeTextRequest()
    request.recognitionLevel = .accurate
    request.usesLanguageCorrection = true
    request.recognitionLanguages = ["en-US"]
    request.minimumTextHeight = 0.003
    let handler = VNImageRequestHandler(url: url, options: [:])
    try handler.perform([request])
    var words: [Word] = []
    for observation in request.results ?? [] {
        guard let candidate = observation.topCandidates(1).first else { continue }
        let string = candidate.string
        var previousEnd = string.startIndex
        var first = true
        string.enumerateSubstrings(in: string.startIndex..<string.endIndex,
                                   options: [.byWords, .substringNotRequired]) { _, range, enclosing, _ in
            // Enclosing range retains punctuation, which matters for IDs/dates.
            let selected = enclosing
            let token = String(string[selected]).trimmingCharacters(in: .whitespacesAndNewlines)
            guard !token.isEmpty else { return }
            guard let tokenRange = string.range(of: token, range: selected) else { return }
            guard let rectangle = try? candidate.boundingBox(for: tokenRange) else { return }
            let b = rectangle.boundingBox
            let separator = first ? "\n" : (previousEnd <= tokenRange.lowerBound ? String(string[previousEnd..<tokenRange.lowerBound]) : "")
            words.append(Word(text: token,
                              box: [Double(b.minX), Double(1-b.maxY), Double(b.maxX), Double(1-b.minY)],
                              confidence: Double(candidate.confidence), separator: separator))
            previousEnd = tokenRange.upperBound
            first = false
        }
        // Some scripts or punctuation-only lines do not enumerate as words.
        if !string.isEmpty && string.rangeOfCharacter(from: .alphanumerics) == nil {
            let b = observation.boundingBox
            words.append(Word(text: string,
                              box: [Double(b.minX), Double(1-b.maxY), Double(b.maxX), Double(1-b.minY)],
                              confidence: Double(candidate.confidence), separator: "\n"))
        }
    }
    let result = try JSONEncoder().encode(Output(words: words))
    FileHandle.standardOutput.write(result)
} catch {
    // Never echo source paths, recognized text, or Vision's error context.
    FileHandle.standardError.write(Data("Local OCR could not process this page.\n".utf8))
    exit(1)
}
'''


def _binary_path() -> Path:
    base = Path(os.environ.get("REDACT_RUNTIME_DIR", Path.home() / "Library" / "Caches" / "Redact" / "runtime"))
    digest = hashlib.sha256(_SWIFT.encode()).hexdigest()[:12]
    return base / f"vision-ocr-{digest}"


def ocr_status() -> dict:
    """A cheap capability check; does not compile or inspect documents."""
    if platform.system() == "Darwin":
        compiler = shutil.which("swiftc")
        ready = importlib.util.find_spec("Vision") is not None or _binary_path().is_file() or bool(compiler)
        return {"available": ready, "engine": "Apple Vision", "offline": True,
                "message": "On-device OCR" if ready else "Run Start Veil.command again to install the Apple Vision bridge."}
    executable = shutil.which("tesseract")
    return {"available": bool(executable), "engine": "Tesseract", "offline": True,
            "message": "On-device OCR" if executable else "Install Tesseract to import scans and photos."}


def prepare_ocr() -> Path:
    """Compile the bundled native helper once, atomically and without a shell."""
    target = _binary_path()
    if target.is_file():
        return target
    with _COMPILE_LOCK:
        if target.is_file():
            return target
        compiler = shutil.which("swiftc")
        if not compiler:
            raise OCRError("Scanned pages need Apple Vision. Install Apple Command Line Tools with xcode-select --install, then retry.")
        target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with tempfile.TemporaryDirectory(prefix="ocr-build-", dir=target.parent) as directory:
            source = Path(directory) / "main.swift"
            binary = Path(directory) / "vision-ocr"
            source.write_text(_SWIFT, encoding="utf-8")
            try:
                result = subprocess.run([compiler, "-O", str(source), "-o", str(binary)],
                                        capture_output=True, timeout=180, check=False)
            except (OSError, subprocess.TimeoutExpired) as error:
                raise OCRError("Apple Vision setup could not finish. Check that Apple Command Line Tools are installed.") from error
            if result.returncode != 0:
                raise OCRError("Apple Vision setup could not compile. Run xcode-select --install, then retry.")
            binary.chmod(0o700)
            binary.replace(target)
        return target


def recognize(image_path: str | Path) -> list[dict]:
    """Return words with normalized, top-left-origin rectangles."""
    path = Path(image_path)
    if platform.system() == "Darwin":
        # Wheels supply the Objective-C bridge on both Apple Silicon and Intel;
        # the normal setup path needs no Swift/Xcode developer tools at all.
        if importlib.util.find_spec("Vision") is not None:
            return _recognize_vision_bridge(path)
        binary = prepare_ocr()
        try:
            result = subprocess.run([str(binary)], input=json.dumps({"path": str(path.resolve())}),
                                    capture_output=True, text=True, timeout=120, check=False)
            if result.returncode:
                raise OCRError("Apple Vision could not read this page. Try a clearer scan or a PDF export.")
            words = json.loads(result.stdout)["words"]
        except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError, KeyError) as error:
            raise OCRError("On-device OCR could not finish this page. Try a clearer scan or smaller document.") from error
        return _validate_words(words)
    executable = shutil.which("tesseract")
    if not executable:
        raise OCRError("This document contains scanned pages. Install Tesseract before importing scans or photos.")
    from PIL import Image
    with Image.open(path) as image:
        width, height = image.size
    try:
        result = subprocess.run([executable, str(path), "stdout", "-l", "eng", "tsv"],
                                capture_output=True, text=True, timeout=120, check=False)
        if result.returncode:
            raise OCRError("Tesseract could not read this page. Check that English OCR data is installed.")
        words = []
        for row in csv.DictReader(io.StringIO(result.stdout), delimiter="\t"):
            token = row.get("text", "").strip()
            if not token:
                continue
            x, y, w, h = (float(row[key]) for key in ("left", "top", "width", "height"))
            words.append({"text": token, "box": [x/width, y/height, (x+w)/width, (y+h)/height],
                          "confidence": max(0.0, float(row.get("conf", 0)) / 100)})
        return _validate_words(words)
    except (OSError, subprocess.TimeoutExpired, ValueError) as error:
        raise OCRError("On-device OCR could not finish this page.") from error


def _recognize_vision_bridge(path: Path) -> list[dict]:
    """Call the installed OS framework directly; no subprocess or compiler."""
    try:
        import Foundation
        import objc
        import Vision
        import re
        words = []
        with objc.autorelease_pool():
            request = Vision.VNRecognizeTextRequest.alloc().init()
            request.setRecognitionLevel_(Vision.VNRequestTextRecognitionLevelAccurate)
            request.setUsesLanguageCorrection_(True)
            request.setRecognitionLanguages_(["en-US"])
            request.setMinimumTextHeight_(0.003)
            url = Foundation.NSURL.fileURLWithPath_(str(path.resolve()))
            handler = Vision.VNImageRequestHandler.alloc().initWithURL_options_(url, {})
            success, error = handler.performRequests_error_([request], None)
            if not success or error is not None:
                raise OCRError("Apple Vision could not read this page. Try a clearer scan or a PDF export.")
            for observation in request.results() or []:
                candidates = observation.topCandidates_(1)
                if not candidates:
                    continue
                candidate = candidates[0]
                line = str(candidate.string())
                previous_end = 0
                for index, match in enumerate(re.finditer(r"\S+", line)):
                    # Vision uses UTF-16 NSRange offsets, including for accented
                    # names and non-BMP characters; Python indexes Unicode scalars.
                    offset = len(line[:match.start()].encode("utf-16-le")) // 2
                    length = len(match.group().encode("utf-16-le")) // 2
                    rectangle, error = candidate.boundingBoxForRange_error_(Foundation.NSMakeRange(offset, length), None)
                    if rectangle is None or error is not None:
                        raise OCRError("Apple Vision could not locate recognized text on the page. Try a clearer scan.")
                    box = rectangle.boundingBox()
                    x, y = float(box.origin.x), float(box.origin.y)
                    width, height = float(box.size.width), float(box.size.height)
                    words.append({"text": match.group(), "box": [x, 1-y-height, x+width, 1-y],
                                  "confidence": float(candidate.confidence()),
                                  "separator": "\n" if index == 0 else line[previous_end:match.start()]})
                    previous_end = match.end()
        return _validate_words(words)
    except OCRError:
        raise
    except Exception as error:
        # Objective-C errors can contain source paths or recognized text. Keep
        # them out of the UI and the process's logs.
        raise OCRError("Apple Vision could not finish this page. Try a clearer scan or run Setup.command again.") from error


def _validate_words(words: list[dict]) -> list[dict]:
    result = []
    for word in words:
        box = word.get("box", [])
        if len(box) != 4 or not isinstance(word.get("text"), str):
            raise OCRError("The local OCR engine returned invalid geometry.")
        import math
        if not all(isinstance(value, (int, float)) and math.isfinite(value) for value in box):
            raise OCRError("The local OCR engine returned invalid geometry.")
        bounded = [max(0.0, min(1.0, value)) for value in box]
        if bounded[2] <= bounded[0] or bounded[3] <= bounded[1]:
            continue
        result.append({"text": word["text"], "box": bounded, "confidence": float(word.get("confidence", 0)),
                       **({"separator": word["separator"]} if isinstance(word.get("separator"), str) else {})})
    return result
