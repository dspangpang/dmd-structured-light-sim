"""Non-structural render presets shared by every simulation branch."""

import os

from variant_api import get_variant


CORE_RENDER_PRESETS = {"default": {}}


def render_presets():
    presets = dict(CORE_RENDER_PRESETS)
    for name, values in get_variant().render_presets.items():
        if name in presets:
            raise ValueError(f"Variant render preset shadows core preset: {name}")
        presets[name] = dict(values)
    return presets


def render_preset_from_argv(argv, default="default"):
    name = os.environ.get("SIM_RENDER_PRESET", str(default)).strip() or str(default)
    for index, key in enumerate(argv):
        if key == "--render-preset":
            if index + 1 >= len(argv):
                raise ValueError("--render-preset requires a preset name")
            name = argv[index + 1]
    return name.strip().lower().replace("_", "-")


def apply_render_preset(cfg, name):
    presets = render_presets()
    try:
        values = presets[name]
    except KeyError as exc:
        choices = ", ".join(sorted(presets))
        raise ValueError(f"Unknown render preset {name!r}; choose one of: {choices}") from exc
    for key, value in values.items():
        if not hasattr(cfg, key):
            raise AttributeError(f"Render preset {name!r} uses unknown runtime field {key}")
        setattr(cfg, key, value)
    cfg.RENDER_PRESET = name
    print(f"[RenderPreset] Applied non-structural preset: {name}")
