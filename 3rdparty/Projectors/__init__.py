from . import ui
from . import projector
from . import operators


def register():
    projector.register()
    operators.register()
    ui.register()


def unregister():
    ui.unregister()
    operators.unregister()
    projector.unregister()
