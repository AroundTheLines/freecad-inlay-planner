# Mac verification summary

The implementation checkpoint passed **33 actual FreeCAD GUI runs, 2,717 assertions, and 227 automated tests** on FreeCAD 1.1.3, macOS arm64. All GUI processes exited normally and ran with network access denied. The finite requirement ledger records 32 required rows passed and optional planning-task restoration unimplemented.

[The public results index](verification-summary.json) records the original run identities, receipt hashes and runtime Python hashes. [The ledger](../tests/requirement_ledger.json) maps requirements to tests. Raw receipts, screenshots, local installation paths and generated machining files remain in the maintainer's local evidence archive; those files are not distributed in this source repository.

The tested pre-publication archive was SHA256 `82c512f17558d7c9272736dae5d1d6788ae50eb7bb01d25fd1cf06680364702a`. Public preparation changes licensing, package metadata and documentation; the 16 runtime Python modules are byte-identical to that checkpoint. These earlier results are not described as a fresh run of a newly built MIT-licensed ZIP. New builds have their own manifests and require matching verification.

## What was exercised

- Complete SVG-to-native-CAM-to-LinuxCNC export for flat, V-bit, bull-nose, flat/V and multiple-flat-tool combinations.
- Native command editing, failed-save recovery, resave, reopen and export without the extension for those five families and a mixed-unit case.
- Independent replay of actual exported motion, original XYZ endpoints and separate XY/Z grids; deliberate precision and motion-mode failures.
- Tool libraries, units, per-operation overrides, per-face operation/change caps, depth/fit parameters, fine selection, modified artwork acceptance and blocked-region overlays.
- Confirmation, cancellation, stale results, document isolation, undo/redo, partial-Apply rollback and worker/filesystem failure recovery.
- Offline installation/update/rollback/removal and measured resource limits, including dense-result rejection followed by recovery.

Native Custom feeds use mm/s and can round during serialization: for example, 500 mm/min can reload as 499.999980 mm/min. Independent checks record that drift separately from deliberate edits. Manually edited paths do not retain the original geometry certificate.

## Limits

Windows, physical wooden cuts and use on a complete 4 GB computer remain unqualified. Existing stock capture supports constant-thickness prisms with irregular outlines or through holes; tapered or recessed solids are rejected. Automatic restoration of an editable planning task is not implemented; saved native CAM paths remain usable. The independent geometry probes and finite GUI matrix cannot prove every possible input or actual machine behavior.

See [the detailed verification approach](VERIFICATION.md), [resource observations](PERFORMANCE.md), and [build/run instructions](../README.md). Original project code and documentation use the [MIT License](../LICENSE); dependencies retain their separate terms.
