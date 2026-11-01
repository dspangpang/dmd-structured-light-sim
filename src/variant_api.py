"""Small branch-variant interface used by the shared core renderer."""


class SimulationVariant:
    """Extension points that do not own camera/projector geometry.

    Child branches may select an allowed camera count, add non-structural
    render presets, add branch-only CLI options and extend the Blender scene or
    capture loop. Device geometry comes from the explicit scene configuration.
    """

    name = "core-sim"
    allowed_camera_counts = (1, 2)
    render_presets = {}

    def configure_defaults(self, cfg):
        return None

    def default_render_request_path(self, repo_root):
        """Return an optional branch-owned default request JSON path."""
        return None

    def apply_render_request(self, cfg, settings, resolve_input_path):
        """Apply validated branch-only settings from ``variant.settings``."""
        if settings:
            keys = ", ".join(sorted(settings))
            raise ValueError(
                f"Variant {self.name!r} does not accept render-request settings: {keys}"
            )

    def apply_capture_request(self, cfg, capture, resolve_input_path):
        """Consume variant-specific fields from the shared ``capture`` block.

        The shared request parser owns common capture options and calls this
        hook for branch-specific extensions. Return the field names consumed
        by the variant so misspelled fields remain rejected.
        """
        del cfg, capture, resolve_input_path
        return ()

    def validate_scene_contract(self, cfg, root, camera_devices):
        count = len(camera_devices)
        if count not in self.allowed_camera_counts:
            allowed = ", ".join(str(value) for value in self.allowed_camera_counts)
            raise ValueError(
                f"Variant {self.name!r} accepts camera count [{allowed}], "
                f"but the App scene contains {count}"
            )

    def parse_cli_option(self, cfg, key, value, parse_bool):
        """Return True only when a branch-specific CLI option was consumed."""
        return False

    def extend_scene(self, cfg):
        """Add branch-only render objects after the common scene is built."""
        return None

    def parameter_metadata(self, cfg):
        """Branch metadata available to parameter and capture exporters."""
        return {}

    def after_scene_built(self, cfg, cameras, sequence_dir):
        return None

    def capture_pre_render_artifacts(self, cameras, cfg, sequence_dir):
        """Generate variant-specific artifacts before the main capture loop.

        Return a mapping from artifact-group names to output-relative record
        lists. The shared renderer validates the files while finalizing its
        manifest.
        """
        del cameras, cfg, sequence_dir
        return {}

    def capture_artifact_groups(self):
        """Return artifact groups generated inside a variant capture loop."""
        return {}

    def prepare_no_render(self, image_files, projector_objects, cfg):
        return None

    def expected_main_camera_file_count(self, image_files, cameras, cfg):
        return len(image_files) * len(cameras)

    def render_capture(
        self,
        image_files,
        projector_objects,
        cameras,
        cfg,
        sequence_dir,
    ):
        """Return True when the branch handled the complete capture loop."""
        return False


def get_variant():
    from variant import VARIANT

    if not isinstance(VARIANT, SimulationVariant):
        raise TypeError("variant.VARIANT must be a SimulationVariant instance")
    return VARIANT
