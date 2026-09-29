# Performance and resource qualification

Public source edition: raw machine-specific receipts referenced below are retained locally. [The results index](verification-summary.json) preserves run identities and hashes; [the summary](FINAL_VERIFICATION.md) explains the tested checkpoint and publication-only changes.

The final Mac package calculated the 24-inch fixture and the 500-region dense fixture, preserved the dense draft when native Apply exceeded its admission budget, and successfully applied a smaller pair afterward. The dense worker exceeded the preferred 512 MiB target. A complete computer with 4 GiB of RAM remains **unqualified**.

## Package and measurement scope

The final retained Mac archive (retained local receipt) has SHA-256 `82c512f17558d7c9272736dae5d1d6788ae50eb7bb01d25fd1cf06680364702a`. Its GUI resource journey and complete unit suite were run on September 28, 2026. The qualification manifest (retained local receipt) verifies all 1,198 archive files against the final extraction and GUI installation, and binds the evidence files by hash.

The standalone worker measurements below were executed on the preceding `fe3e54589e5997e279503c25ea0d020c4e70dc02c6bb5a48bd6d40a9d1d72ea7` archive that day and are **reused through exact byte equivalence**, not presented as standalone reruns of the final package. The equivalence receipt (retained local receipt) compares every archive member: only `panel.py` and its build manifest changed. All 15 other addon modules, 1,166 vendor files, four dependency-wheel SHA-256 values, the wheel lock, and example fixtures are identical. It also verifies the prior worker source snapshots and original report hash. The final GUI journey and all 227 unit tests, including the 22 lifecycle tests, were newly executed on the final package.

The host was an arm64 Mac running macOS 26.5.2 with **128 GiB RAM**, FreeCAD 1.1.3, bundled Python 3.11.14, and Qt 6.8.3. Actual workers and the GUI ran with network access denied. These are single qualification runs on powerful development hardware, with other validation work possible concurrently; they are not repeated, controlled speed benchmarks or low-end hardware certification. All memory units below are binary: MiB = 2²⁰ bytes, GiB = 2³⁰ bytes.

The worker report samples the worker and its descendants every 50 ms. Worker self-reported high water uses `getrusage` immediately before final JSON serialization; external measurement includes startup and serialization. Short-lived peaks can fall between samples. The final GUI runner sampled its entire process tree every 100 ms and recorded no monitoring errors. GUI current RSS was unavailable inside the offline sandbox; the GUI's own figures are explicitly high-water measurements, while the external runner records sampled process-tree RSS.

## Budgets and implementation

| Budget | Meaning in this build |
| --- | --- |
| 512 MiB worker | Preferred soft target; missed by the dense fixture. |
| 768 MiB worker | Reactive calculation guard, polled every 200 ms; not an OS-enforced hard ceiling. |
| 2 GiB application | Engineering budget for GUI plus worker activity and native Apply staging. The measured GUI journey remained below it; this is not a universal bound. |
| 128 MiB serialized result | Controller rejects a larger result before loading it for review. Requests are limited to 64 MiB. |
| 4 GiB whole computer | Product target; operating-system load, swapping, responsiveness, and competing software have not been qualified on such hardware. |

Motion records use temporary-file-backed 40-byte rows in the worker. Geometry works with bounded linework batches and analytic depth sections, without a stock-sized resolution raster or voxel volume. Exact repeated cutter sweeps reuse section results. Canonical hashing and worker JSON output stream records. IPC still uses a JSON file, and the GUI materializes motion dictionaries for review; this is not end-to-end zero-copy transport. Native CAM construction also adds objects and commands beyond the compact worker representation.

The worker guard is active during calculation and stops before final streamed serialization; the external audit monitors the full process lifetime. The final package's real memory-pressure test exited with code 74 after **4.22 s**, at a sampled **790.89 MiB**. That observed overshoot demonstrates the polling guard's actual behavior. It must not be described as a strict 768 MiB allocation cap. See the pressure-test receipt (retained local receipt).

## Standalone packaged worker

All ten cases passed their expected outcomes on the preceding archive; the module and dependency equivalence above binds these unchanged computational measurements to the final archive. Positive results passed the production geometric verifier and returned a proposal requiring explicit acceptance. The two blocked fixtures are successful diagnostic tests; they did not produce a geometric certificate or machining paths. These resource measurements do not replace the separate independent geometric audits.

| Fixture | Worker wall time, s | Self high water, MiB | Sampled process-tree peak, MiB | Motion records | Outcome |
| --- | ---: | ---: | ---: | ---: | --- |
| Small flat cutter | 1.42 | 63.30 | 63.19 | 1,225 | Review proposal |
| V-bit only | 1.68 | 59.72 | 59.72 | 3,467 | Review proposal |
| Bull-nose only | 0.65 | 48.78 | 48.77 | 4,560 | Review proposal |
| 24-inch stock | 0.87 | 80.22 | 80.22 | 6,386 | Review proposal |
| Asymmetric leaf | 0.78 | 64.97 | 64.77 | 5,936 | Review proposal |
| Outlined letterform | 0.60 | 52.62 | 52.50 | 3,789 | Review proposal |
| Scattered motif | 0.64 | 58.59 | 58.48 | 4,025 | Review proposal |
| Fine-detail ladder | 0.16 | 40.62 | 10.95 | 0 | Expected blocked result |
| Boundary-crossing ornament | 0.16 | 40.31 | 10.25 | 0 | Expected bounds rejection |
| Dense decorative field | 47.00 | 697.50 | 695.81 | 202,328 | Review proposal |

Each wall time includes a fresh solver process and result serialization. SVG imports for the six corpus cases run separately and retain their own receipts; their import time is not included in the table. Exact requests, outputs, fixture dimensions, source hashes, import receipts, and monitoring records are retained in the worker audit (retained local receipt).

The small cutter-family fixtures use 12 × 12 × 6 mm stock, a 6 mm polygonal circle, a 2 mm cutter, 1 mm inlay depth, and 0.05 mm XY/Z resolution. The V-bit has a 90° included angle and 0.1 mm tip; the bull nose has a 0.25 mm corner radius. The 24-inch fixture uses 609.6 × 609.6 × 19.05 mm stock, a centered 140 mm square, a 12.7 mm flat cutter, 3 mm inlay depth, and 0.01 mm XY/Z resolution.

The dense SVG has **500 regions and 10,000 outer-boundary vertices**, on 200 × 160 × 6 mm stock. It uses a 1 mm flat cutter, 0.4 mm stepover, 0.5 mm inlay depth, and 0.05 mm XY/Z resolution. Its serialized result was 37,300,219 bytes (**35.57 MiB**) and its allocated motion storage was **11.25 MiB**. Geometry intermediates and review data, rather than the packed coordinates alone, account for much of the working set. The [fixture recipes](../tools/run_resource_audit.py) define the full settings.

## Actual GUI review, memory rejection, and recovery

The new offline GUI resource journey (retained local receipt), executed on the final `82c512f1…` archive, passed **143 assertions** in **128.25 s**. It imported SVGs, entered settings, calculated, accepted modifications, confirmed Apply, and retried through actual task-panel controls. It did not inject solver results or bypass the memory guard.

| Stage | Calculate and accept, s | Accepted motion records | Accepted worker high water, MiB | GUI high water at stage, MiB | Result |
| --- | ---: | ---: | ---: | ---: | --- |
| 24-inch stock | 1.87 | 6,384 | 72.19 | 652.39 | Reviewed and ready; no native Apply attempted for this stage |
| Dense 500-region design | 99.29 | 202,328 | 581.42 | 1,670.61 | Reviewed; native Apply rejected before document writes |
| Smaller 8 mm key on 20 mm stock | Not timed separately | 1,464 | 46.72 | 1,670.61 | Both native CAM Jobs applied in the same task |

Calculate and accept includes two calculations, so those times should not be compared directly to a single worker run. The complete GUI-and-worker process tree reached a sampled **1,838,841,856 bytes = 1,753.66 MiB = 1.713 GiB**. The unchanged high-water figure after the smaller retry does not mean recovery allocated no memory.

The GUI's 24-inch fixture imports and centers an SVG; the standalone fixture constructs a square directly in setup coordinates. They are distinct input representations of the same-sized square and produced 6,384 and 6,386 motion records respectively. Both passed verification; their command streams are not asserted to be identical. See the GUI large-case result (retained local receipt) and the exact standalone request in the worker evidence.

Native Apply estimates staging, destination objects, and undo overhead before writing the document:

```text
predicted bytes = GUI historical high-water RSS + 128 MiB + motion_count × 8192
```

The 8,192-byte allowance is a conservative engineering estimate, not a measured universal per-command cost. In this dense run it predicted **3,543,449,600 bytes = 3.300 GiB**, above the 2 GiB admission budget. The rejection receipt (retained local receipt) confirms the dense draft was preserved, document objects were unchanged, and no staging document or active transaction remained. The recovery receipt (retained local receipt) confirms both smaller board and plug Jobs were subsequently created. This qualifies safe handling of the dense case; it does **not** demonstrate native Apply or export of its 202,328 motions.

## Lifecycle and remaining qualification

The final complete test suite (retained local receipt) passed **227 tests**, with zero failures or skips, in 28.60 s; JUnit records 28.484 s. Its **22 lifecycle tests** used the final extracted package and exercised the real bundled Python worker and Qt `Calculation` controller: malformed requests, invalid schema/action/hash/nonfinite values, dependency failure, worker crash, memory failure, partial files, stale run IDs, cancellation, timeout, forced termination of an unresponsive worker, failed process startup, duplicate starts, and exclusion of a second simultaneous calculation. Failure cases verified cleanup and a subsequent successful retry. The unit receipt (retained local receipt) binds the run to the final archive and a stable source snapshot. These headless controller checks do not stand in for the separate GUI document-preservation test above.

An earlier post-installation run failed 13 harness assertions because importing FreeCAD placed the user's installed addon ahead of the intended test package. The failed-run evidence (retained local receipt) is retained. The test harness now gives each subprocess its own FreeCAD profile, selects the audited package after FreeCAD initialization, and asserts both the exact controller module path and isolated data directory. The final 227-test run passed with the extension still installed; no production code was changed for this harness fix.

Existing selected stock must be a **constant-section prism** beneath its planar face. Irregular boundaries, holes, and conservatively captured curved outlines are supported within that model. Tapered solids, changing sections, and hidden underside recesses are explicitly rejected. A planar face alone does not qualify an arbitrary solid.

Windows execution, physical wooden fit, machine cutting behavior, and a complete 4 GiB computer remain outside this qualification. The dense soft-target miss, JSON materialization in the GUI, and conservative native admission estimate remain concrete optimization work. Formal timing benchmarks should use repeated runs on specified hardware after those changes. Displayed machining-duration estimates use constant entered feeds, exclude manual tool changes, and do not model machine acceleration; the calculation wall times here measure software work instead.

## Reproduction

Run from the repository root on a Mac with the installed FreeCAD bundle and the project's development `.venv`. Actual process-tree monitoring and GUI execution must be permitted; the child tests themselves deny network access. Use fresh output directories. The GUI runner serializes cases with its session lock.

```sh
.venv/bin/python -m zipfile -e \
  artifacts/final-resource-package-82c512f1/InlayPlanner-0.1.0-macos-arm64.zip \
  artifacts/performance-repro

.venv/bin/python tools/run_resource_audit.py \
  --addon artifacts/performance-repro/InlayPlanner \
  --out artifacts/performance-worker --timeout 240

INLAY_AUDIT_ADDON_ROOT="$PWD/artifacts/performance-repro/InlayPlanner" \
  .venv/bin/python -m pytest -q \
  --basetemp artifacts/performance-unit \
  --junitxml artifacts/performance-unit.xml

.venv/bin/python tools/run_e2e.py \
  --archive artifacts/final-resource-package-82c512f1/InlayPlanner-0.1.0-macos-arm64.zip \
  --case resource_guard --offline --timeout 480 \
  --out artifacts/performance-gui
```

The final qualification manifest (retained local receipt) separates the reused standalone worker report from the newly executed final unit and GUI receipts and contains their full hashes. The equivalence receipt's SHA-256 is `8101ac64905abc623e61e23f7cdef09cae4ee498a7c5d25483fd59e2e94a3a95`. The primary report hashes are:

| Evidence | SHA-256 |
| --- | --- |
| Worker resource report, reused by byte equivalence | `3efa8872cd5b3644196bc0377d495a8567acc3490dc367fd0540e323f136b240` |
| Complete unit JUnit, including fresh lifecycle tests | `176ed444dc1667f732409664ec7635186cacd5675970518695a412b1db8c70dd` |
| Final GUI resource report, newly executed | `41f80771e9218109d4cb2966bc749b0cd88cb1f5509fc4c7498c1ca446346b18` |
