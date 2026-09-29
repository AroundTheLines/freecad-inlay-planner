# Implementation contract

All modules target the verified FreeCAD 1.1.3 macOS arm64 runtime (Python 3.11.14, Qt 6.8.3, OCCT 7.8.1). Pure worker dependencies never load in FreeCAD's GUI process. JSON values are finite numbers in mm, mm/min, seconds, and rpm. Inputs and outputs have `schema_version: 1`.

`solver.solve(spec, progress=None, cancelled=None) -> dict` consumes the following keys. Callbacks receive progress dictionaries; cancellation returns a boolean. Validation failures are returned as blocked diagnostics rather than partially applicable output.

```
revision: integer; input_hash: SHA256 of canonical spec except input_hash
style: flat | v | bull
artwork: {regions: [{id, name?, polygons: [{outer: [[x,y],...], holes: [[[x,y],...],...]}]}]}
excluded_region_ids: [source id,...]
exclusions: [polygon,...]  # canonical artwork coordinates, explicit paired masks
accepted_proposal_hash: null | SHA256
faces: {
  board|plug: {
    stock: [width,height,thickness], bounds: [xmin,ymin,xmax,ymax],
    design_to_face: [a,b,c,d,e,f], origin: [x,y,z], clearance_height: mm,
    limits: {operations: null|positive integer, tool_changes: null|nonnegative integer}
  }
}
resolution: {xy: positive mm, z: positive mm}
fit: {driver: depth|height, value: mm, glue_gap: mm, side_clearance: signed mm, backing_clearance: mm}
tools: [{id,number,name,shape:flat|v|bull,diameter,cutting_length,
         corner_radius?,included_angle_deg?,tip_diameter?,shank_diameter?,
         max_stepdown,stepover,feed,plunge_feed,travel_feed,spindle,
         native_definition?: {native ToolBit serialized fields}}]
selected_tool_ids: [physical id,...]
overrides: {operation_id: {feed?,plunge_feed?,travel_feed?,spindle?,max_stepdown?,stepover?}}
travel_mode: feed | controller_rapid
```

Artwork has already been scaled into physical canonical XY coordinates. Face transforms use `x'=a*x+c*y+e`, `y'=b*x+d*y+f`. The board's transform normally is translation; the plug's normally mirrors X about the design centre and translates onto its stock. This is the surface map of the physical flip, not an additional output mirror. Origins are positions in each local stock frame; output moves are `face_xy-origin_xy`, `z=-depth-origin_z`. Display units are separate from physical values. GUI metadata may add `source` (embedded source text/hash), `placement`, and `existing_faces`; the planner ignores display-only values.

Output:

```
schema_version,input_hash,revision
status: ready | needs_acceptance | blocked | cancelled
derived: {depth,height,seating_depth}
operations: [{id,face,tool_id,purpose,predecessors,settings,
  moves: [{kind:rapid|feed,role:cut|plunge|travel,x,y,z,feed_mm_min}],
  time:{cut_s,travel_s,baseline_s,optimized_s}}]
per_face_counts: {board|plug:{operations,tool_changes}}
diagnostics: [{code,severity,face?,region_ids,message,polygons}]
proposal: null | {hash,polygons,difference_polygons}
preview: {source_polygons,retained_polygons,cuttable_polygons,blocked_polygons,
          excluded_polygons,board,plug,sections}
verification: {status,method,error_budget,checks,...}
resource_stats: {...}
```

Diagnostic polygons and the preview's source/retained/cuttable/blocked/excluded polygons use canonical artwork XY, including when a diagnostic identifies a particular face. The UI applies that face's transform once. A wholly blocked component cannot simultaneously be classified as cuttable.

Preview board/plug surfaces use `{vertices:[[x,y,depth],...],faces:[[i,j,k],...]}` in local stock coordinates with positive depth; coarse display data never decides safety. Sections contain named polylines in canonical XY/depth, with actual versus reference gaps labeled. The UI may show an empty surface with a diagnostic while a blocked draft is being inspected. Paths expose every coordinate and effective feed. Native adapter converts feeds into FreeCAD mm/s and Z sign is already final.

`native.apply_pair(doc, spec, result, fail_after_face=None) -> {board: job_name, plug: job_name, metadata: object_name}` validates input hash and ready status, uses one transaction, preserves unrelated work, and creates native built-in objects only. A failure aborts both faces. `native.list_tools()` returns snapshots plus source library information without editing libraries. `native.capture_face(selection)` returns a validated setup record for a planar selected face; explicit existing-face topology restrictions must be diagnosed, not silently approximated.

Large motion/preview arrays may later use task-scoped NPY files while retaining logical fields. Coordinate snapping, geometry uncertainty, user-accepted modification, and display approximation are distinct. Any API change must be reported to the lead before consumers are changed.
