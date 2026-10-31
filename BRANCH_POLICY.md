# Maintenance and releases

The standalone repository uses `main` for reviewed source and version tags for
releases. Features and fixes are developed on topic branches. A release must
pass Python checks, both actual Blender CPU examples and the pixel-coordinate
test. Record the tested Blender/Python versions and the source revision.

The initial release was prepared on an isolated branch of the original
simulator. The research checkout continues to use its own pinned commit.
The public repository starts with a reviewed source snapshot and preserves its
upstream attribution in `SOURCE_PROVENANCE.md`; private branch history and
experiment data are not published with it.

After release, shared fixes should be developed here and explicitly adopted by
research projects at pinned revisions. Do not silently update experiment
submodules or reinterpret historical outputs with new geometry conventions.
