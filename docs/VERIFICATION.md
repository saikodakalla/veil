# Verification notes

Synthetic data only. Verified on the build Mac (Apple Silicon, Python 3.12, local MPS inference).

- Complete API/document/detection suite: 28 passing tests with VEIL_TEST_MODEL=1, including three real-model tests; default run has 25 passing tests and 3 skipped opt-in tests.
- Actual model suite: 3 passing tests, sockets/urllib blocked; searchable PDF and scan -> OCR/model -> page masks -> export; exported-pixel OCR checks identifier removal and preserved clinical terms.
- Long-document test uses actual transformer predictions beyond 15,000 characters.
- All 200 safetensors tensors were checked equal to the original Stanford v2 checkpoint before selecting the safe checkpoint.
- Legacy Torch 2.2.2 / numpy 1.26.4 runtime verified with actual offline CPU inference. Intel hardware itself was not available.
- Browser: first-run surface, sample inference, finding exclusion, manual box addition/removal, review gate and downloaded PDF verified. Mixed TXT, DOCX, scanned PNG, and a two-page PDF all completed real inference in one browser import. Page switching, zoom, and two-document ZIP export (three total pages) passed. Desktop 1440px and narrow-screen 390px review, policy, and engine surfaces were visually checked without horizontal page overflow.
- Launcher run from the repository with bundled UI. First dependency installation and later cached launch paths tested. Source ZIP was extracted into a fresh folder containing spaces, and its launcher was run with Node removed from PATH; it created a new isolated environment from the hashed lock and served its bundled interface.

These are synthetic regression checks, not clinical validation on a representative medical-record dataset. Detection quality for a new organization/layout requires its own evaluation.
