"""One command integrated into CAM without changing the built-in workbench."""
from pathlib import Path

import FreeCADGui as Gui


class CreateInlay:
    def GetResources(self):
        return {'Pixmap':str(Path(__file__).parents[1]/'resources'/'inlay.svg'),
                'MenuText':'Create Inlay…',
                'ToolTip':'Create matching board and plug paths from filled SVG artwork.'}

    def IsActive(self):
        return True

    def Activated(self):
        from .panel import show_panel
        return show_panel()


class CamCommands:
    def modifyMenuBar(self):
        return [{'insert':'Inlay_Create','menuItem':'CAM_Job','after':True}]

    def modifyToolBars(self):
        return [{'insert':'Inlay_Create','toolItem':'CAM_Job','after':True}]


def register():
    if 'Inlay_Create' not in Gui.listCommands():
        Gui.addCommand('Inlay_Create',CreateInlay())
    if not hasattr(Gui,'_inlay_planner_manipulator'):
        Gui._inlay_planner_manipulator=CamCommands()
        Gui.addWorkbenchManipulator(Gui._inlay_planner_manipulator)

