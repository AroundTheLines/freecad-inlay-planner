# Inlay Planner for FreeCAD CAM

An MIT-licensed extension for FreeCAD **1.1.3 on macOS arm64**, using Python 3.11 from that FreeCAD bundle. It generates a matching board pocket and backed plug from filled SVG regions. See the [verification summary](docs/FINAL_VERIFICATION.md) for completed checks and limits. This repository publishes source; it does not claim Windows support or physical-cut qualification.

## Install and open

Build the offline archive as described below. Extract `InlayPlanner` from `dist/InlayPlanner-0.1.0-macos-arm64.zip` into the `Mod` directory under the user application data directory reported by `FreeCAD.getUserAppDataDir()`. Restart FreeCAD, choose CAM, then choose **Create Inlay…** beside CAM's Job command. The extension adds a command and native Task panel, not another workbench.

No runtime service, account, paid tool, network request, or external Python installation is required. Native worker dependencies are private to the add-on and do not replace FreeCAD's dependencies. This particular archive includes arm64 wheels; it must not be installed as an Intel or Windows build.

## Make a pair

1. Set board and plug slab dimensions, or select a planar face of an existing constant-thickness wooden solid and capture it for that setup. Irregular outlines and through holes are preserved. Each face is treated as its own 3-axis setup. Select each setup's origin, rectangular machining bounds and margins. Operation and tool-change limits apply separately to each face.
2. Import a filled SVG. Convert text to outlines first. Review unsupported content rather than assuming it was converted. Set physical artwork size, center on either axis, rotate, or move it in the preview. Board and plug use one paired artwork revision; the plug transform includes the physical flip.
3. Select flat end mills, V-bits, or bull-nose cutters from FreeCAD's current library, or enter a local cutter. A bull nose has a flat center and rounded corners; ball noses are outside this version. Enter cutting, plunge, and travel feeds, spindle speed, maximum depth per pass, and pass spacing. These settings are local snapshots and do not edit the tool library. Refresh reloads changed library geometry.
4. Set bottom glue gap, normal clearance per mating side, and backing clearance independently. Drive either inlay depth or plug height; the other follows `height = depth − glue gap + backing clearance`. Set the machine's XY movement increment and separate Z increment. These are motion resolution, not positioning accuracy.
5. Calculate. Review cuttable, blocked, excluded, and proposed changed regions, the static removal mesh, the assembled section, and the expected finished surface. Zoom and select regions or paths, including individual paths in the 3D view. Hiding a path affects display only. Excluding a region or rectangular portion affects both mating halves and requires recalculation.
6. Quantization and cutter access may require a quantified paired geometry change. Accept it explicitly and recalculate. Included blocked regions prevent applying. Changing settings invalidates the result. Review operation counts, motion-only times, and individual operation settings; per-operation overrides are available.
7. Choose **Review and apply pair…** and confirm the displayed settings. The extension creates two native CAM Jobs atomically. No plug-backing removal operation is generated. Save the FreeCAD document, inspect or edit the native CAM operations, and export using FreeCAD CAM's normal postprocessing command. LinuxCNC is the verified initial postprocessor target.

The current preview meshes and centre section are labeled display approximations. Planning checks use conservative continuous-stock geometry and final quantized motion, not display pixels. A positive fit clearance identifies a nominated assembly pose; it does not imply a unique self-seating position or account for wood compression.

## Native files and editing

Applied tool geometry, stock, and native Custom paths are embedded. Saved jobs can be reopened, edited and exported in the same FreeCAD version without the extension, original SVG, or original tool library. Metadata preserves the calculation input for inspection. Automatic regeneration from a reopened file is not currently exposed; the editable native CAM paths are the primary output. Manual CAM changes do not carry the extension's earlier verification result forward.

Closing a draft leaves already applied jobs unchanged. Undo the Apply transaction to remove a newly applied pair. Calculation runs in a separate cancellable process. Failure or cancellation preserves the draft and does not apply a partial pair. A changed/deleted source face requires a fresh capture.

## Update or remove

Close active inlay tasks and save your work. Keep a copy of the existing `Mod/InlayPlanner` directory before replacing it with a new verified archive, then restart FreeCAD. Remove only that directory to uninstall. Native saved CAM jobs remain in the document and need no extension to export. Never remove the entire user `Mod` directory or edit unrelated preferences.

## Build and verify

On an arm64 Mac with the supported FreeCAD installed, use its bundled Python:

```sh
/Applications/FreeCAD.app/Contents/Resources/bin/python -m venv .venv
.venv/bin/python -m pip install -r requirements-test.txt -r requirements-worker.txt
.venv/bin/python tools/prepare_worker.py --download
.venv/bin/python tools/build_package.py
```

The first build downloads the exact hash-locked worker wheels into `.cache/wheels` and stages them under `vendor`. Subsequent builds can omit `--download`. Installed runtime use is offline. `tools/build_package.py` produces the ZIP and a per-file SHA256 manifest. These pinned wheels target macOS arm64; building on another platform does not establish compatibility.

Run unit and analytic tests with `.venv/bin/python -m pytest`. Run `tools/run_native.py` for isolated real-FreeCAD portability, editing and native export checks. `tools/run_e2e.py` installs the actual ZIP into a fresh profile and operates the real Qt interface. It requires a GUI-capable local session and records screenshots, input/output files, process exit status and externally measured process-tree memory. A crashed or timed-out process fails even if it wrote a success record first.

Tests do not drive a CNC machine. Windows, physical wooden test cuts, and usability on a complete machine with only 4 GB of RAM remain separate validation. The development Mac's measured memory is not proof of the whole-machine target. Runtime guards reject work that exceeds the calculation budget instead of silently dropping artwork or reducing machining fidelity.

## License

Copyright © 2026 Qile Wang and Inlay Planner contributors. Original project code and documentation are available under the [MIT License](LICENSE), with no warranty and a limitation of liability. Copies and substantial portions must retain the copyright and license notice. Commercial use and modification are permitted. Third-party components retain their separate [licenses and notices](THIRD_PARTY_NOTICES.md).

Package metadata follows FreeCAD's [package format documentation](https://github.com/FreeCAD/FreeCAD-documentation/blob/main/wiki/Package_Metadata.md). Publication in this repository does not imply inclusion in FreeCAD's Addon Manager catalog.
