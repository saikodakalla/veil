# Veil design system

Veil is a local document workbench. Its audience is reviewing medical records at a desk under normal ambient light; use light document surfaces, a deep evergreen navigation rail, and high contrast text.

The surface operates rather than advertises. A compact sidebar anchors Documents, Redaction policy, and Local engine. A generous work area puts import and the current batch first; populated state becomes a document viewer with an adjacent findings inspector.

Use the native system sans stack, fixed type sizes, 14px body, 12px supporting text, and a 36px page title. Colors: canvas #f5f6f5, white document surfaces, ink #202923, muted #68736b, evergreen #173e31, mint #e9f0e9, border #e0e5df. Primary actions are evergreen. Redaction bars are pure black. Use amber for pending review and red for failures.

Use 8px control radii, 12px section radii, thin borders, 36–40px controls, and 24–36px space between task groups. Document previews may have an understated paper shadow. Avoid ornamental metrics and charts. Status colors always accompany text. The drop target is the signature interaction: its page motif becomes a real preview when files arrive.

Desktop: 218px navigation rail; main content fills the remaining width. Review: file list above the viewer, with a 290px findings column. At tablet sizes collapse the rail to compact navigation and stack inspector below the viewer; at phone sizes use a compact top navigation and vertical flow.

All assets, fonts, scripts and icons ship locally. No remote imagery, fonts, trackers, or scripts. Hover and focus communicate affordance; transitions use 160ms. Respect reduced motion. Mask drawing uses normalized coordinates; findings toggles update previews and invalidate review approval.
