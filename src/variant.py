"""Single-camera runtime shared by both DMD raster types."""
from variant_api import SimulationVariant
from pathlib import Path


class DmdSimulationVariant(SimulationVariant):
    name = "dmd-structured-light-sim"
    allowed_camera_counts = (1,)

    def after_scene_built(self, cfg, cameras, sequence_dir):
        self._output = Path(sequence_dir)
        self._camera_names = {camera.name for camera in cameras}
        self._suffixes = []
        if cfg.SAVE_RENDER_FLOAT_EXR:
            self._suffixes.append("_float.exr")
        if cfg.SAVE_DEPTH or cfg.SAVE_POINTCLOUD:
            self._suffixes.append("_depth.exr")
        if cfg.SAVE_POINTCLOUD:
            self._suffixes.append("_pointcloud.ply")

    def capture_artifact_groups(self):
        records = []
        for image in sorted(self._output.rglob("*")):
            if not image.is_file() or image.stem not in self._camera_names:
                continue
            for suffix in self._suffixes:
                artifact = image.with_name(image.stem + suffix)
                if not artifact.is_file():
                    raise RuntimeError(f"Missing requested render output: {artifact}")
                records.append({"path": artifact.relative_to(self._output).as_posix()})
        return {"auxiliary_captures": records} if records else {}


VARIANT = DmdSimulationVariant()
