# FreeCAD Inlay Planner

Product concept · Consolidated discovery draft · Updated 27 September 2026

This document defines the product experience, inputs, outputs, and acceptance expectations. It does not choose an architecture, geometry algorithm, programming language, or implementation sequence. **Confirmed** requirements reflect the owner's decisions. **Recommended** or **proposed** behavior remains a product recommendation. The working name is provisional.

The owner has authorized the agent to update FreeCAD on the Mac and then implement and verify the extension. The current implementation and verification phase is **macOS only**. Windows remains a future platform objective; Windows access, an agent handoff, packaging, and tests are deferred and cannot block the current Mac work. [Tech Plan.md](<Tech Plan.md>) governs the authorized implementation and its verification.

## 1. Purpose, audience, and scope

Help a woodworker take SVG artwork and their available CNC cutters, generate matching board and plug toolpaths, export them through ordinary FreeCAD CAM, cut the two workpieces, and slot the plug directly into the board using their chosen fit settings. Backing removal and final finishing happen afterward through the user's workshop process.

The audience is **woodworkers with FreeCAD and CAM experience**. The extension simplifies the paired inlay calculation and preview while leaving ordinary CAM inspection, editing, and export familiar and accessible.

### Confirmed product boundaries

| Area | Requirement |
| --- | --- |
| FreeCAD | Latest stable release; no legacy FreeCAD support required. |
| Desktop platforms | macOS in the current implementation and verification phase. Windows support and verification are deferred. |
| Materials | Wooden board and plug; the woods may differ. |
| Workpieces | Default dimensioned slabs, from a few inches across to about 2 ft across initially. |
| Faces | One board face and one matching plug face per inlay task; flat faces aligned for three-axis machining. Use another task for another pair. |
| Plug construction | One plug wood on a shared blank. A backing may hold disconnected design elements together for glue-up. |
| Artwork | SVG filled vector regions, including text already converted to outlines. |
| Artwork modification | Users may explicitly accept a matching machinable version of the artwork, with changes applied to both halves and differences from the source visible. |
| Cutters | Flat end mills, V-bits, and bull-nose cutters. Both metric and imperial tools are supported. |
| Inlay styles | V-carved when suitable selected tools are available, and useful non-V inlays with flat end mills or bull-nose cutters. |
| Fit | Separate glue-gap, side-fit, and backing-clearance inputs, configurable by the user. Side fit is clearance per mating side, measured normal to the wall; positive values open the fit. |
| Depth relationship | The user may enter inlay depth and have plug height calculated, or enter plug height and have inlay depth calculated. Fit settings participate in the calculation. |
| Machine resolution | One shared XY resolution and a separate Z resolution. |
| Machining settings | User-entered cutting/travel speeds and tunable spindle speed; per-tool maximum depth per pass with operation overrides. |
| Preview | Static material removal and depth views; visible cuttable, uncuttable, and explicitly excluded regions; fine selection through zooming. |
| Interface | A Create Inlay command accessible alongside CAM, opening a native Task panel with the normal 3D viewport available. |
| Planning objective | Optimize each toolpath use independently. Manual tool-change time is excluded from estimates. Users choose the tools for their desired fidelity. |
| Count controls | Optional maximum operation count and maximum tool-change count independently for each face, with each face's actual counts visible during review. |
| Applying paths | Explicit confirmation after calculation. Partial designs require explicit paired exclusions. |
| Output | Native FreeCAD CAM work that remains usable on FreeCAD installations without this extension. |
| Export | Entirely handled by normal FreeCAD CAM tools; outside the extension's scope. LinuxCNC is the initial validation workflow, not an extension-owned export restriction. |
| Regeneration | Desirable when reopening with the extension installed, but secondary to usable, editable CAM results. |
| Distribution | Fully open source and supplied without warranty. |
| Hardware | Usable on a low-power CPU/GPU, targeting 4 GB of RAM for the whole machine, including the OS, FreeCAD, and the extension. Numerical optimization benchmarks will be defined after implementation is complete. |

The central result is a reviewed pair of machinable shapes for the retained artwork. A plausible-looking cutter path alone is insufficient: the preview must communicate the material that remains and whether the two halves can assemble under the entered settings.

## 2. Vocabulary and everyday workflow

An **artwork region** is a filled area selected from the SVG, including its holes. It is different from a **toolpath**, which describes cutter motion. An **operation** is a selectable CAM task, such as pocket clearing or boundary finishing; it can contain several passes.

A **toolpath use** is one operation using one cutter in one setup. The same cutter used on the other workpiece, or for another operation, has a separate use and time estimate. A **setup** identifies stock placement, machining orientation, and the user-chosen work origin. Using a cutter at another speed does not create another physical tool.

The normal journey is:

1. **Start an inlay.** Launch **Create Inlay** alongside CAM to open the native Task panel. Enter board and plug slab dimensions. Alternatively, select existing planar geometry in the document. Establish one face pair and choose each work origin.
2. **Import the SVG.** Review physical size, filled regions, holes, and unsupported or defective content. Select the artwork to include.
3. **Place it on the slab.** Center horizontally, vertically, or both. Adjust scale, bounds, margins, position, and rotation in the preview. Review the matching plug orientation.
4. **Select the tools.** Browse the existing FreeCAD tool library and choose available cutters. Review per-tool pass limits, speeds, and any operation overrides. Optionally set independent operation-count and tool-change limits for each face.
5. **Set depth and fit.** Choose inlay depth or plug height as the driving dimension. Set glue gap, side fit, and backing clearance independently, then inspect the computed mating geometry.
6. **Calculate and inspect.** Generate a static material-removal/depth preview and individually optimized toolpaths. Highlight cuttable and uncuttable regions. Show time per operation, tool assignments, actual operation/tool-change counts for each face, and any conflict with that face's enabled count limits.
7. **Refine the selection.** Change tools, scale, bounds, depth, or fit and recalculate. Review and explicitly accept a matching modified design where useful. If some artwork is omitted, explicitly exclude it from the pair and review the remaining design.
8. **Apply the pair.** Confirm the current calculated revision, accepted artwork modifications, and exclusions. The resulting board and plug CAM work becomes available for ordinary CAM editing and saving.
9. **Continue in CAM.** The user exports using FreeCAD's existing postprocessing workflow, machines both workpieces, and assembles them. The extension generates no backing-removal or final surfacing operation.

Returning to an earlier stage should preserve other valid settings. A tool change in the preview does not require starting the project again. Recalculation is an explicit, acceptable part of the workflow.

## 3. SVG input and representative artwork

### Supported input

The required input is filled vector geometry. Text must already be converted to outlines. Closed contours, curves, basic vector shapes, compound filled regions, and holes should be represented accurately enough to assess machinability. Groups and layers can assist selection without forcing each contour into a separate CAM operation.

The import view should:

- Show dimensions and units. Honor declared physical size when unambiguous; otherwise let the user set a known width or height before calculating.
- Preserve the relationship between outer boundaries and holes, including counters in outlined letters such as A, B, and O.
- Identify stroke-only artwork, live text, and raster content as unsupported input. Do not silently turn those into cuttable regions.
- Explain self-intersections, open boundaries, duplicate edges, or ambiguous overlapping fills at their locations. The required baseline is clear diagnosis and selection; a general SVG repair editor is not required.
- Identify visual effects whose appearance cannot be represented by supported filled geometry. Supported vector regions in a mixed file must remain distinguishable from unsupported content.
- Treat colors and layers as selection aids. Selected inlay regions use one plug wood; color does not automatically imply another wood, depth, or operation.

Preserve the source artwork for comparison. Scaling, accepted artwork modifications, explicit exclusions, and tool-related geometric differences must remain distinguishable from it. Changing the source file must not silently change an already accepted machining result.

### Generated example set

The owner has delegated example selection. The following SVG set is proposed for later generation as product examples and functional checks; no owner-supplied artwork is required. These are vector fixtures, not raster illustrations, and have not been generated or machined as part of this document.

| Example | What it should exercise |
| --- | --- |
| Asymmetric leaf emblem | Curves, rotation, centering, mirrored plug orientation, and an easily recognized assembled result. |
| Outlined letterform with counters | Nested holes and islands, using vector outlines with no live font dependency. |
| Scattered geometric motif | Disconnected regions kept together on a shared plug backing; selecting and excluding individual elements. |
| Fine-detail ladder | Features of decreasing width and spacing, exposing differences between selected cutters and XY/Z resolutions. |
| Boundary-crossing ornament | Artwork extending beyond a rectangular permitted area, including explicitly excluded portions and recalculation of the retained pair. |
| Dense decorative field | Many regions and curves for qualitative responsiveness checks and later performance benchmarks. |

Scale examples across the supported stock range. Representative cutter diameters can include 1/16 in, 1/8 in, and 1/2 in—1.5875 mm, 3.175 mm, and 12.7 mm respectively—alongside independently sized metric cutters. V angles and bull-nose corner radii can be selected when the examples are built. These examples do not define a restrictive tool catalog.

## 4. Slabs, face pairs, units, and origins

### Starting from slabs

The default entry point creates simple rectangular board and plug stocks from width, height, and thickness. The user should not have to model a cutting board first. Existing FreeCAD geometry remains a useful alternative for an experienced CAM user.

The initial supported scale is from a few inches across to about 24 in / 609.6 mm across. This describes the intended first-release use range, not a new immutable dimension cap beyond the user's selected bounds. Thickness, wood species, and finished fit are user settings; this concept imposes no universal wood recipe or minimum thickness.

Support metric and imperial stock dimensions and tool definitions, including a mixture in one project. Changing displayed units must preserve physical dimensions, clearances, and resolutions. A metric cutter must not be replaced by the nearest imperial nominal size, or vice versa.

### One face pair per task

Each inlay task coordinates one board face and one plug face. Both are planar and oriented for three-axis access in their respective setups. Another face pair belongs to another inlay task, which may coexist in the same FreeCAD document.

The user chooses each work origin. Show the origin, work axes, stock face, and depth direction in the preview. Artwork centering is independent of the machine origin: centering or resizing artwork must not silently relocate the origin.

The matching plug must have the correct physical scale and assembly orientation even when its stock size, origin, or placement differs from the board. Show machining orientation and assembled orientation clearly. In particular, asymmetric artwork and outlined letters must read correctly after the plug is flipped into place.

## 5. Placement, bounds, selection, and preview

### Placement controls

Horizontal and vertical auto-centering, scale, and bounds controls belong directly in the preview workflow. Recommended details are:

- **Center horizontally**, **Center vertically**, and **Center both**, measured along the slab face's axes and independent of the camera angle.
- Start centered on both axes. Make active centering visible; scaling can retain it until the user manually moves the artwork on an axis.
- Numeric width, height, scale, position, and rotation alongside direct manipulation. Preserve aspect ratio by default; non-uniform scaling is an explicit user choice.
- Separate outlines for the stock, artwork extents, and allowed rectangular bounds. Support rectangular bounds and edge margins initially.
- An explicit fit-to-bounds action that displays the resulting physical size. Changing bounds alone must not silently crop the design.
- Matching scale across both halves while allowing different positions on their individual blanks.

Check the artwork and the material that must be removed around the plug against the relevant bounds. A design outline fitting inside a rectangle does not by itself establish that all necessary cutter motion and clearing fit. Visualize those differences where they affect the result.

### Selection and machinability states

The user controls selection granularity and can zoom in for fine selection. Support selecting regions and smaller visible portions where they can form a valid retained design. Selection must correspond to the geometry, rather than impose an arbitrary minimum screen-sized area.

Use three distinguishable states:

| State | Meaning |
| --- | --- |
| Cuttable and included | The selected tools and machine resolution can produce the retained geometry within the chosen bounds. Positively highlight it. |
| Uncuttable and included | Some required geometry is blocked by bounds, the selected tools, resolution, or a mating inconsistency. Highlight the cause and affected location. |
| Explicitly excluded | The user has removed this artwork from the paired design. Keep it restorable and visually distinct from both included states. |

Where only part of a region is cuttable, show both portions. Selecting a marked area should explain the relevant reason, such as cutter diameter, corner radius, reach, XY/Z resolution, or bounds. Indicate whether it affects the board, plug, or both. Do not imply that adding a tool resolves an unrelated bounds or resolution issue.

Users may review a proposed machinable modification alongside the source. Accepting it updates the retained design on both halves; machinability states then describe that retained design, while differences from the original remain visible. Proposed modifications do not change included geometry until accepted.

Changing selected tools, bounds, scale, or machining settings may require a full recalculation. This is acceptable. Preserve the user's placement and other inputs, mark the calculated result stale, and update both halves when calculation finishes.

### Static views

The first release needs static material-removal and depth previews. Animated cutter playback, photorealistic rendering, and fixture simulation are not required. Recommended views are:

- Artwork and allowed bounds on each stock.
- Original artwork compared with the predicted finished result.
- Material remaining after selected operations, including plug backing, islands, rounded floor transitions, and residual material.
- Individual selectable toolpaths, with cutting and non-cutting motion distinguished.
- A section through the assembled pair showing depth, plug height, glue gap, side fit, and backing clearance.
- The expected board after external backing removal, without implying that a surfacing toolpath has been generated.

Selection in the operation list and viewport should identify the corresponding tool, workpiece, and path. Support zoom, isolate, and hide/show controls. Labels and patterns should complement color.

**Visibility, selection, and inclusion are different states.** Hiding an operation for inspection does not remove it from the plan. Excluding it from the output changes the machining sequence and requires review of the remaining pair.

## 6. Tool library, pass settings, and speeds

### Tool choice

Use the existing FreeCAD library. Show recognizable tool names, actual shapes, dimensions, and whether each cutter is available and selected for the current task. A tool present in the library is not automatically available on the user's machine.

The initial supported cutters are flat end mills, V-bits, and bull-nose cutters. A bull nose has rounded corners around a flat central end; it is not a ball nose. Tools outside the supported shapes may remain visible in the library but should be identified as unsupported for this inlay workflow.

Required tool information depends on the shape: cutting diameter and reach, V included angle and tip geometry, or bull-nose corner radius. Unknown dimensions needed to assess the cut must be identified rather than invented. Support metric and imperial definitions without nominal-size substitution.

The user decides how many tools to involve for fidelity. Adding a selected cutter can recover detail; removing one can expose uncuttable regions. The planner must not drop a detail cutter merely to lower the combined time. It may explain that a selected tool has no useful role instead of generating redundant work for it.

FreeCAD's documentation notes that many CAM operations do not automatically account for arbitrary cutter shapes. The product's cutter support must therefore mean a preview and cutting result appropriate to the actual shape. This is a product requirement, not an assumption that native operation assignment alone supplies it. [FreeCAD CAM workbench documentation](https://github.com/FreeCAD/FreeCAD-documentation/blob/main/wiki/CAM_Workbench.md)

### Per-tool settings and overrides

Maximum depth per pass is a per-tool setting, with operation overrides. It is distinct from total inlay depth and from the cutter's physical reach. The depth preview should show the result of the current settings and overrides.

Recommended behavior is to keep pass spacing with the same reusable per-tool settings and allow operation overrides. Show inherited values and overrides clearly. The extension follows the current configured values; changing an operation should not silently alter the shared library or every use of that tool.

Cutting feed, non-cutting travel speed, and spindle speed are user-entered and independently tunable. Expose plunge feed where relevant, and allow different board/plug or roughing/finishing settings. Selecting a wood species does not automatically choose speeds.

Travel inputs and estimates must reflect the behavior of the resulting CAM motion. For example, LinuxCNC distinguishes programmed-feed motion from machine-controlled rapid motion. The UI must not claim an entered travel value overrides a controller's rapid configuration when it does not. Export remains FreeCAD CAM's responsibility. [LinuxCNC motion documentation](https://linuxcnc.org/docs/html/gcode/g-code.html)

## 7. Inlay styles, independent fit controls, and linked depth

### Supported machining styles

| Style | Expected behavior |
| --- | --- |
| V-carved inlay | Generate coordinated tapered board/plug geometry using a suitable selected V-bit. Other selected tools may perform compatible clearing work. |
| Flat-end-mill inlay | Generate a matching non-V pair, accounting for reachable corners and any accepted boundary differences. |
| Bull-nose inlay | Use the actual corner radius for compatible clearing and mating geometry, including useful bull-nose-only cases where the selected cutter can make a valid pair. |

The presence of a V-bit does not override the user's chosen style. No supported cutter is promised to reproduce every SVG. Unreachable portions are highlighted and may be explicitly excluded from the pair.

The user may explicitly accept a matching machinable modification of blocked artwork, such as rounding a sharp corner on both halves. Preview both halves and their differences from the original SVG before acceptance. The accepted version becomes the retained design, with source differences still visible. This complements explicit paired exclusions; every accepted modification must remain feasible with the selected tools, machine resolution, bounds, and fit settings. No universal detail tolerance is imposed.

Matching corner compensation matters on both halves. Standard straight-inlay workflows account for cutter radii so the insert fits its pocket. [Vectric inlay documentation](https://docs.vectric.com/docs/V12.0/VCarveDesktop/ENU/Help/form/Create%20Inlay%20Toolpath/index.html)

Recommended initial behavior uses the same selected finishing geometry for corresponding mating boundaries, with separate clearing tools where useful. This does not require identical feeds or operations on both woods. Different finishing geometries or mixed V/straight styles within one face pair need not be part of the initial workflow.

### Three independent fit inputs

Expose three separate, independently editable variables:

| Input | Meaning shown in the UI |
| --- | --- |
| Glue gap | Intended bottom space between the seated plug and the corresponding pocket floor, where a floor exists. |
| Side fit | Clearance per mating side, measured normal to the corresponding wall. Positive values open the fit. Label the direction of measurement and its effect on both halves. |
| Backing clearance | Separation between the board's top face and the surrounding plug backing/relief surface when assembled. |

Fit is personal configuration. The product must not impose a universal acceptable seam, preferred glue allowance, or wood-specific fit recipe. Preserve user values and show the resulting assembly, including interference or clearances that cannot be resolved with the selected tools and machine resolution.

**Confirmed side-fit convention:** show clearance per mating side, measured normal to the corresponding wall, with positive values opening the fit. For parallel vertical walls, 0.10 mm per side gives 0.20 mm total width clearance. Display the resulting visible separation in the section and finished-surface views, and label the measurement explicitly.

The three values are independently editable, but their geometric effects can interact, particularly with tapered seating. Independently editable controls must not imply that every combination is achievable. Explain conflicts without silently changing another fit setting.

### Inlay depth and plug height

The user chooses which dimension drives the pair:

- **Drive from inlay depth:** enter the intended board cavity depth; compute the matching plug height using the fit inputs and selected tooling.
- **Drive from plug height:** enter the intended plug height; compute the matching inlay depth using the same relationship.

Only one is the driving input at a time. Show the other as a calculated value; switching the driver should preserve the current physical pair until a value changes. Changing any fit control updates the calculated counterpart and both previews.

The UI must depict the reference planes. Recommended definitions are inlay depth from the selected board face to the intended cavity depth, and plug height from its surrounding machined relief plane to its protruding mating geometry, excluding sacrificial backing thickness. Show seating depth separately as an assembly result.

A V-carved feature can have varying local depth. Show nominal values alongside a section through the selected feature, rather than implying every narrow region has the same flat floor. Tapered seating, bottom glue space, and backing clearance are distinct relationships. [Vectric VCarve inlay documentation](https://docs.vectric.com/docs/V12.5/Aspire/ENU/Help/page/single-page/)

The final appearance should be consistent with the planned external removal of the backing. The extension does not generate that finishing operation or predict wood compression and finishing technique. A calibration example may be useful documentation, but a universal fit target is not a prerequisite for this concept.

## 8. Resolution, bounds, fidelity, and adjustable limits

### The two absolute planning constraints

The owner identifies **machine resolution** and **user-selected image bounds** as the absolute constraints. The planner must not silently relax either to make a path possible.

Use two resolution inputs:

- **XY resolution:** shared by X and Y.
- **Z resolution:** independent of XY.

Resolution is the smallest movement the machine can make, not a guarantee of physical positioning accuracy. Assess both halves at the supplied resolutions; a proposed fit or feature must not depend on finer movement than the machine can provide. Changing units must preserve the physical resolution.

Fidelity depends on the machine, its resolution, and the selected cutters. Do not impose a separate universal minimum feature width or acceptable visual error as a product rule. Show the expected differences from the SVG and let the user choose tools, scale, settings, or exclusions.

### User settings and physical feasibility

Other values—fit, depth, speeds, pass limits, and optional count budgets—are editable user settings. The planner still honors their current values; optimizing a path is not permission to exceed an entered per-tool pass depth or alter a fit allowance.

The selected cutter's actual geometry determines what can be cut. An impossible mating shape is shown as uncuttable rather than made valid by an override. This is an assessment of the selected tools, not an extra universal product constraint.

Do not introduce mandatory machine-specific minimum floor thickness, finish prescriptions, clamp models, or a global machining-time ceiling. Show stock and material-removal consequences so an experienced CAM user can make those choices. Rectangular bounds and margins are the required initial boundary controls; irregular keep-out editing and fixture simulation are not required.

### Confirmed count controls

The existing tool selection already controls the number of tools involved. Provide an optional maximum generated-operation count and an optional maximum tool-change count independently for each selected face. Show the board face's counts against its limits and the plug face's counts against its limits. These two count controls are confirmed for the initial interface. Additional pass-count or time-limit controls are not required.

Each face's limits can be configured, enabled, or disabled independently; they are separate from optimizing time inside each toolpath. If a chosen limit conflicts with the selected tools or required work, identify the affected face and report the conflict without dropping a tool, omitting necessary work, or hiding operations. The user must revise the plan or change/disable the conflicting limit before applying. A visible enabled/disabled state avoids an unexplained default cap.

A tool change is a transition between physical cutters. A → B → A involves two tools and two changes after the initial tool is loaded. Changing feeds while retaining A is not a tool change. Operation count is distinct from the number of depth or coverage passes inside an operation.

The scope is **per face**, since board and plug workpieces may be machined separately. Count generated operations and cutter transitions within that face's setup, excluding its initial tool loading. A transition between machining the board and machining the plug does not count toward either limit. Unused allowance on one face cannot offset an exceeded limit on the other. For example, three operations on each face satisfy a four-operation limit on each face, even though the pair contains six operations. Any combined count shown is informational and has no shared cap.

## 9. Independent optimization and time estimates

Optimize each toolpath use separately for its cutting and non-cutting motion time, with its cutter, machining purpose, required result, starting material state, and user settings held consistent.

Within that use, seek efficient coverage, cutting order, travel, entries, and retracts. Respect the work already performed by preceding required operations. Dependencies such as clearing before finishing still establish a valid sequence; independent optimization cannot assume another operation's work is dispensable.

The user controls tool count for fidelity. The optimizer must not trade away a detail cutter or selected geometry for a lower sum of times. It must not raise the user's speeds, change fit, or exceed the entered pass limits to appear faster.

Show estimated time for each operation, with cutting and travel distinguishable where useful. A board, plug, or combined subtotal is simply the sum of those path estimates. **Exclude manual tool changes, workpiece setup, glue-up, backing removal, and external finishing.** Do not label the sum as complete workshop elapsed time.

Show required cutter changes separately for each face without assigning them manual-change time or making their reduction an optimization objective. Board and plug may be machined independently; no cross-setup ordering is assumed.

Recalculate affected timings when speeds, tools, coverage, bounds, scale, or depth change. Identify estimate assumptions such as unknown acceleration. Numerical performance and optimization benchmarks will be chosen after implementation is complete; this concept requires the objective and honest reporting without inventing targets now.

## 10. Design acceptance, partial designs, and confirmation

### Accepted artwork modifications

Allow the user to inspect a proposed modification, compare it with the original SVG, and accept it for the matching board and plug geometry together. Acceptance establishes the retained design; the current calculated paths must correspond to it before Apply. The original artwork remains available for comparison and restoration, and accepted changes remain identified in the final review.

Changing tools or other inputs can produce a new proposed modification, which requires review and acceptance before it replaces the retained design. This can use the existing preview and confirmation workflow. A geometry change must not be applied silently because the user previously accepted a different version.

### Explicit exclusions

The user may apply toolpaths for cuttable regions after explicitly excluding any remaining blocked artwork from the paired design. Where a modified version has been accepted, assess that retained version for machinability.

Recommended interaction:

1. Select the region or portion to exclude, using zoom for precise selection.
2. Apply that exclusion to the corresponding board and plug artwork together.
3. Retain the excluded source geometry in a distinct, restorable state.
4. Recalculate boundaries, holes, mating geometry, toolpaths, and times for the retained design.
5. Show any new problem caused by the exclusion, such as a newly unreachable corner or invalid retained boundary.
6. Allow application once all retained regions are feasible and the exclusion list is visible in the confirmation.

A visible uncuttable marker does not itself exclude a region. An arbitrary partial selection must still produce valid retained geometry and a matching pair; explain when it does not. Restoring an excluded region triggers the appropriate review and recalculation.

### Review lifecycle

Use clear states such as Draft, Calculating, Ready for review, Applied, and Needs recalculation. While the user moves or scales artwork, the placement overlay should remain useful; material-removal and depth results may wait for **Recalculate**. Automatic full toolpath regeneration during dragging is not required.

Calculation should show progress and allow cancellation. A failed or canceled recalculation should leave the prior accepted result distinguishable and usable. Changed tools, stock, bounds, depth, fit, or resolution must not leave stale paths presented as current.

Before applying, show a compact review of:

- Board/plug stocks, selected faces, work origins, dimensions, and bounds.
- Driving depth or height, its calculated counterpart, and all three fit variables.
- Included operations, cutters, settings/overrides, each face's counts and enabled limits, and per-path estimates.
- Accepted artwork modifications, explicit exclusions, and the expected retained artwork compared with the source.
- Whether the action creates new CAM work or replaces previously generated work.

**Apply board and plug paths** commits the reviewed pair to the document. It does not export machine files or start a CNC. Canceling leaves existing accepted and unrelated CAM work intact. Applying revised work should be undoable through normal FreeCAD behavior.

## 11. Native CAM output, portability, and reopening

### Required output and export boundary

The required deliverable is native FreeCAD CAM work for the board and plug, with meaningful names, tools, stock/setup context, and paths the user can inspect and edit in CAM.

The extension ends at that output. FreeCAD CAM owns file grouping, postprocessor selection, machine dialect, final G-code export, and any other supported downstream format. The user follows their existing export workflow. LinuxCNC remains the initial end-to-end validation route; the extension must not build its own exporter or restrict ordinary CAM export choices to LinuxCNC.

This follows FreeCAD's division between a Job containing stock, tools, and ordered operations, and CAM postprocessing producing the machine-specific output. [FreeCAD CAM Job](https://github.com/FreeCAD/FreeCAD-documentation/blob/main/wiki/CAM_Job.md), [FreeCAD CAM Post](https://github.com/FreeCAD/FreeCAD-documentation/blob/main/wiki/CAM_Post.md)

### Portability is a core requirement

Saving a FreeCAD document must preserve usable CAM output on another supported FreeCAD installation **without the inlay extension installed**. Optional inlay planning state must not prevent opening the document, inspecting paths, ordinary CAM-level editing, saving, or exporting through CAM.

The saved machining result must not silently depend on an inaccessible source SVG, the creator's personal tool-library files, or the inlay extension being loaded. Necessary geometry and tool information must remain usable with the output. Exactly how this is achieved is a later implementation decision.

The portability obligation covers current supported FreeCAD installations; it does not introduce legacy-version support. Normal receiving-machine CAM/postprocessor configuration remains the user's responsibility.

FreeCAD documents stored non-parametric paths whose edits can be retained independently of their source. That supports distinguishing editable machining output from full parametric regeneration; it is not proof that this extension's future documents are portable. That must be verified. [FreeCAD CAM Simple Copy](https://github.com/FreeCAD/FreeCAD-documentation/blob/main/wiki/CAM_SimpleCopy.md)

### Optional regeneration and presets

Reopening with the extension installed should ideally restore artwork, accepted modifications, selection/exclusions, placement, tool choices, per-face count limits, depth/fit settings, and allow regeneration. This convenience must not take priority over portable CAM output. If regeneration is unavailable, the existing paths remain the useful result and ordinary CAM editing remains available.

If regeneration is supported, it must not silently overwrite native CAM edits made since the last calculation. Make replacement explicit and preserve the prior result until the user applies the regenerated pair. Exclusions must not silently disappear when tools or source files change.

Recommended reusable settings are per-tool pass settings and speeds, XY/Z resolution, slab defaults, and personal fit values. Their exact persistence behavior can be settled later, subject to the portability requirement. A separate exported setup sheet is not required; normal CAM properties and readable labels can carry the essential context.

## 12. Confirmed FreeCAD interface

Provide a **Create Inlay command accessible alongside CAM, opening a native Task panel**. This is the confirmed first-release experience: launch from the CAM workflow, select and preview in the normal viewport, apply the board and plug paths, then continue with ordinary CAM editing and export.

This choice follows familiar FreeCAD conventions. Commands can appear in toolbars and menus; the native Task panel supports tool parameters and selection from the tree or 3D view. Custom toolbars can expose commands from multiple workbenches. These conventions support the selected experience without prescribing an implementation mechanism. [Task panel](https://github.com/FreeCAD/FreeCAD-documentation/blob/main/wiki/Task_panel.md), [Workbench creation](https://github.com/FreeCAD/FreeCAD-documentation/blob/main/wiki/Workbench_creation.md), [Interface customization](https://github.com/FreeCAD/FreeCAD-documentation/blob/main/wiki/Interface_Customization.md)

### Task-panel experience

Keep the 3D view available throughout. Organize inputs into compact groups for stock/origins, artwork/bounds, tools/pass settings, depth/fit, and calculated results. Provide independent operation-count and tool-change limits for the board and plug faces, clearly labeled by face. Show each face's actual counts beside its limits and results. Keep the current stage visible without requiring a long succession of modal dialogs.

Use **Calculate/Recalculate**, **Apply board and plug paths**, and **Cancel** for their stated actions. Parameter edits update the draft. Applying creates the reviewed CAM results. The normal CAM tools remain the route to further edits and export.

Follow familiar units, selection, object naming, properties, undo/cancel, and keyboard conventions. Do not replace the user's layout or make the preview a separate graphics environment. Keep fine zoom/selection available rather than covering the viewport with a modal window.

Extension packaging and command registration remain later technical decisions. They must preserve the confirmed CAM-accessible command and native Task-panel experience.

## 13. Platform, distribution, and responsiveness

Support the latest stable FreeCAD on macOS in the current phase. Windows remains a deferred platform objective and requires its own future verification before any Windows support claim. The agent owns the authorized Mac application update; the owner is not required to perform it. Target ordinary low-power desktop or laptop CPU/GPU hardware with static previews and no dedicated-GPU requirement added by the extension. The confirmed memory target is 4 GB for the whole machine, including the operating system, graphics, FreeCAD, and all extension processes. The existing FreeCAD hardware requirements still apply.

Keep placement and selection responsive while calculation is pending. Prefer explicit recalculation, progress, cancellation, and clear stale-state handling over making every edit trigger expensive work. A coarse display, if needed during interaction, must be labeled and must not be presented as precise evidence of mating clearance.

The owner will define numerical benchmarks for optimization after implementation is complete. There is no required owner benchmark machine, timing target, or performance dataset to supply before product planning can finish. The generated artwork set can provide the starting examples when that stage arrives.

The project is fully open source and supplied without warranty. The selected license is MIT, with copyright/license notice retention and warranty/liability disclaimers. Normal add-on installation and updates through FreeCAD's ecosystem are recommended. Core work uses local SVGs and tool data; fully offline operation with no required service/account is the recommended default, with online access limited to optional installation/updates.

## 14. Observable acceptance scenarios

These are acceptance requirements for the authorized Mac implementation, not reports of completed tests. Exercise the entire software workflow in the actual FreeCAD GUI, starting with a packaged installation and continuing through import, configuration, preview, review, Apply, native editing, save/reopen, and ordinary CAM export. Check successful and rejected inputs, recovery, and all exposed branches of these requirements; independently inspect the resulting geometry and exported motion. The technical plan maps every requirement to observable tests and recorded evidence. Finite tests cannot prove every possible SVG, tool, or machine combination; report the tested input envelope, actual results, and gaps without claiming universal coverage.

| Scenario | Evidence of the intended result |
| --- | --- |
| CAM entry point | Launch Create Inlay alongside CAM, configure it in a native Task panel while selecting and previewing in the normal 3D view, then apply the pair and continue in CAM. |
| Ordinary V-inlay | A generated asymmetric SVG with holes and disconnected regions produces a reviewed board/plug pair that assembles in the expected orientation after machining. |
| Non-V inlay | Representative flat-end-mill and bull-nose-only cases produce valid pairs using the actual cutter shapes; unsupported portions are clearly shown. |
| Mixed units and scale | Metric and imperial cutters coexist without physical-size changes; examples work from a few inches to about 2 ft across. |
| Slab placement | The user creates slabs without prior CAD modeling, centers artwork on either axis, adjusts scale/bounds/margins, and preserves the chosen work origins. |
| Face pair | One task coordinates one planar board/plug pair. A second pair can be created separately without changing the first. |
| Depth and fit | Glue gap, side fit, and backing clearance are independently editable. Side fit is per-side normal clearance, with positive values opening the fit. Either inlay depth or plug height can drive the computed counterpart; reference planes and geometric conflicts are visible. |
| XY/Z resolution | Shared XY and independent Z inputs affect both halves. Changing units preserves them; required geometry finer than the supplied resolution is identified. |
| Pass settings | A tool's maximum depth per pass is reused across operations, and an explicit operation override changes only that use. Current settings remain visible in review. |
| Fine selection and exclusions | Zoom enables detailed selection. Explicit exclusions update both halves and leave valid retained regions. Included blocked regions still prevent application. |
| Accepted artwork modification | A sharp detail that the selected tools cannot reproduce can be replaced with an explicitly accepted, matching machinable version on both halves. Source differences remain visible; rejecting a proposal preserves the retained design. A later geometry change requires renewed review. |
| Tool iteration | Changing the selected cutters updates cuttable/uncuttable highlighting, material removal, depth views, operations, and timings after recalculation. |
| Independent optimization | Each toolpath is optimized with its selected cutter and settings. Adding a user-selected detail tool retains its work even if the sum of times increases. Manual changes are excluded from timing. |
| Count controls | Each face has independent operation and tool-change limits and visible actual counts. Three operations per face satisfy a four-operation cap on each face; unused allowance cannot offset an exceeded cap on the other face. Initial tool loading and transitions between workpieces are excluded. A conflict identifies the face and requires a user change before Apply without silently removing tools or work. |
| Confirmation and recovery | Calculate produces reviewable results; Apply commits them. Cancel/failure leaves prior accepted work usable and unrelated CAM work intact. |
| Native output portability | Save on the supported Mac installation with the extension, open in a fresh supported Mac profile without it, inspect and edit the CAM result, save it again, and export through ordinary CAM. Cross-platform document exchange belongs to later Windows verification. |
| Complete Mac workflow | Run the actual installed command and Task panel through the full input-to-native-export journey, including every supported cutter family, independent geometry/output checks, invalid-input handling, cancellation, rollback, and install/remove/reopen behavior. Unrun scripts, screenshots alone, and mocked calculations do not establish success. |
| Optional regeneration | If provided, reopening with the extension restores planning state and permits reviewed regeneration. Its absence does not compromise existing native CAM output. |
| Export boundary | The user uses normal CAM postprocessing, initially exercised with LinuxCNC. The extension owns no exporter and creates no backing-removal path. |
| Finished physical result | With suitable selected tools and the user's chosen fit settings, the woodworker machines both halves and slots the plug directly into the board. External glue-up/backing removal follow their normal practice. |
| Modest hardware | The workflow remains usable on low-power hardware, with static previews, progress, and cancellation. Numerical optimization benchmarks are established after implementation. |

A physical demonstration is still needed to substantiate successful fitting; previews alone do not prove that the extension has achieved the final woodworking outcome. The acceptance target is correct behavior for the user's settings, not a universal seam specification.

## 15. Initial exclusions and future possibilities

The initial product does not include curved surfaces, multi-axis machining, multiple face pairs within one inlay task, multiple plug woods/assembly stages, ball-nose cutters, automatic bitmap tracing, live-text conversion, stroke expansion, animated cutting simulation, fixture simulation, or backing-removal/final-surfacing paths.

It also does not provide its own export formats, postprocessors, CNC sender, or machine control. LinuxCNC validation does not change that boundary.

Loose-insert release/placement, multiple inlay woods, nesting/batches, additional cutter shapes, richer preview playback, and replacement-plug workflows can be considered later. Their possible future value must not complicate the initial slab-to-CAM workflow or weaken portable output.

## 16. Product readiness and intentional deferrals

The core product decisions are settled: a CAM-accessible command with a native Task panel, per-side normal clearance, explicit acceptance of matching modified artwork, and independent operation-count/tool-change limits for each face are confirmed. There are no remaining blocking product questions in this concept.

The owner has authorized the Mac update followed by autonomous implementation and thorough end-to-end software verification on that Mac. No Windows setup or verification is a current prerequisite or completion gate. A later Windows phase must establish its own native evidence; current Mac results must not be relabeled as Windows compatibility.

The project uses the MIT license. Preserve its copyright and license notices in distributed copies; third-party components retain their own terms. Public source publication is authorized.

Recommended defaults for otherwise unspecified product details are one coherent inlay style per task, rectangular bounds/margins, local/offline processing, and standard native CAM handoff. The owner has delegated representative SVG and cutter-example selection. No additional artwork upload or universal finished-fit target is needed.

Numerical performance benchmarks are intentionally deferred until implementation is complete. Dependency choices, geometry algorithms, object types, file-storage arrangements, installation mechanisms, and implementation tasks are specified separately in [Tech Plan.md](<Tech Plan.md>). Document portability must be demonstrated by implementation tests; it is a required outcome, not an assumed property of every FreeCAD extension.

This document remains the product contract. The separate technical plan controls the authorized implementation, records planned versus executed verification, and preserves the distinction between Mac software completion and later Windows, constrained-hardware, and physical-cut validation.
