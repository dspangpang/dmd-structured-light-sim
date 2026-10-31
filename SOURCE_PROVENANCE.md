# Source provenance

## Renderer baseline

The renderer originates from Jia Zhizhou's `blender-stereo-projector-sim`
repository, branch `diamond-dmd-sim`, at commit
`c550b431515ba37702dac957d35fb5d53f181e44` (2026-09-12).

The initial standalone version preserves the shared scene, projection,
calibration and rendering interfaces. Release preparation adds standalone
configuration, the two DMD examples, input preparation and validation, and fixes
the checkerboard entry's indentation. It also adds render failure propagation,
requested auxiliary-output validation, camera artifact hashes, and primitive
material/rotation options. Changes were
prepared on a separate worktree; the original experiment submodule was not
updated.

## Diamond adapter

The DLP4500 mapping functions in `src/dmd_dlp4500.py` were extracted from the
working-tree file
`paper_sim_pipeline/diamond-dmd/experiments/core/generate_dlp4500_blender_textures.py`
in the author's gwCamSDK project on 2026-10-31.

- Original file SHA-256: `0c58ccd57c632694cba898424c556739dffb72707f2436ed20e998512fc33a87`.
- Parent repository HEAD at extraction: `1d42cb9ccbbc066531520154d64b173d48ad8a7f`.
- That parent commit is context, **not** a claim that the source file exists at
  that path in the commit: extraction used a working-tree snapshot after local
  directory reorganization.

The extracted library removes SDK-relative path and CLI handling while retaining
the coordinate, carrier layout and per-pixel mapping functions. It needs only
the standard library and Pillow. Its transforms and complete pixel permutation
are covered by the standalone tests.

## Authors and third-party code

The original simulator history identifies Jia Zhizhou as the project author.
The vendored Projectors add-on is by Jonas Schell, with its source commit,
modifications and GPL notice retained separately. See `THIRD_PARTY_NOTICES.md`.

The public repository starts from a curated source snapshot rather than the
private development history. No experiment images, target meshes, manuscript
assets, runtime caches or machine-specific configuration files are included.
