# Projectors Vendor Notes

Source: https://github.com/Ocupe/Projectors

Vendored from commit: 405dce83cb7d537e88423c8de80fd5526aac2f51

License: GPL-3.0-or-later, preserved in `LICENSE`.

Local changes:

- Projector settings callbacks can resolve the owning projector through
  `proj_settings.id_data`, so scripted updates do not depend on the selected
  Blender object.
- Added `set_custom_texture_image()` and `refresh_projector_geometry()` for
  custom image sequences that need to use each image's actual resolution.
