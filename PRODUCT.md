# Veil

<!-- impeccable:product-schema 1 -->

## Platform

web

## Users

People preparing medical records for sharing, including a Mac user receiving this repository from a friend. The supplied concept mentions clinics.

## Product Purpose

Bulk import documents, use a downloaded model to infer sensitive information locally, review the proposed redactions, and export permanently redacted documents with solid black bars matching the supplied third image.

## Operating Context

Runs on the user's own device in a browser connected to a loopback Python service. Model and dependency installation require internet once; document processing must not send documents to an internet service.

## Capabilities and Constraints

Local clinical model inference, scanned-document OCR, bulk input, visual review, manual masks, black-bar PDF export, and a reproducible Mac setup. Never imply that an automatic detector guarantees removal of all identifiers. The source repository must exclude uploaded records and model weights.

## Evidence on Hand

Three supplied screenshots: a clinic-oriented local redaction concept, Distil PII model listings, and a black-bar redacted page. No real patient records were supplied. Demonstration records must be labeled synthetic.

## Product Principles

- Document contents stay on the user's device.
- Detection and permanent removal are separate, inspectable steps.
- Preserve the useful clinical document layout.
- Make a friend's first launch repeatable.

## Confirmed Priorities

The user confirmed mixed PDFs and scanned records as the primary inputs, and all personal identifiers with maximum coverage as the default policy. All detection categories are enabled by default.

## Open Decisions and Assumptions

 English detection is the initial scope; it is not a multilingual assurance. Veil is a working name chosen for this build. Use a restrained professional desktop interface so bulk operation and review remain legible.
