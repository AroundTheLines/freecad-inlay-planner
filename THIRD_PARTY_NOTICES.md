# Third-party components

The private worker distribution includes these unmodified upstream wheels and their license files:

| Component | Version | License notice in this package |
| --- | --- | --- |
| NumPy | 2.2.6 | `vendor/numpy-2.2.6.dist-info/LICENSE.txt` (includes bundled-library notices), plus NumPy subcomponent notices |
| Shapely | 2.1.2 | `vendor/shapely-2.1.2.dist-info/licenses/LICENSE.txt` |
| GEOS, bundled by Shapely | As recorded in the Shapely wheel | `vendor/shapely-2.1.2.dist-info/licenses/LICENSE_GEOS` |
| svg.path | 7.1 | `vendor/svg_path-7.1.dist-info/licenses/LICENSE.txt` |
| defusedxml | 0.7.1 | `vendor/defusedxml-0.7.1.dist-info/LICENSE` |

The wheels' complete `.dist-info` metadata and notices are retained. The build manifest records every included file and each source wheel's SHA256. Shapely's runtime dependency is NumPy; the other listed worker packages have no additional runtime Python dependencies. Optional test/documentation dependencies are not installed in the runtime package.

FreeCAD, Python, Qt/PySide, Coin, and Open CASCADE are supplied by the user's FreeCAD installation and are not redistributed in the add-on ZIP. The development test tools (pytest, Hypothesis, and psutil) are not part of the installed add-on.

Original Inlay Planner code and documentation use the MIT license in `LICENSE`.
Third-party components retain their own licenses; MIT does not relicense them.
This source repository does not include the wheel binaries. The locked build
downloads their exact upstream archives and preserves all included notices.
Binary redistributors must also satisfy those components' requirements,
including GEOS's LGPL terms; retaining a notice alone does not replace those
obligations.
