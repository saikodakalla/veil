# Synthetic redaction samples

These fictional records contain invented identities, reserved 555-01xx telephone numbers, example.com email addresses, and explicitly invalid test identifiers. They are not real patient records.

Upload the TXT samples if supported, or use the companion PDFs and scan images generated from them. The referral TXT contains a form-feed character between its two sections so it can render as two pages. Scan variants can test OCR and redaction across pages.

Review the output against `expected-identifiers.json`: inspect every occurrence of the listed values, including repeated names, phone numbers, clinicians, organizations, and continuation-page references. Confirm that useful clinical text remains legible. Redaction behavior depends on the enabled categories and processing path; these expected values are a review checklist, not a guarantee that a model will detect every item.

Use only these fictional fixtures for testing. No messages, referrals, or appointments described in them were sent or created.
