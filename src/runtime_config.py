from dataclasses import dataclass


@dataclass
class RuntimeConfig:
    """Blender render state after importing an external App scene.

    Device intrinsics and pose/projection matrices stay unset until
    ``app_scene_config.apply_app_scene_config`` injects the authoritative
    current simulation scene scene. They are adapter fields, not Blender
    parameter defaults. User-facing scene variants must never be implemented by
    subclassing this class.
    """

    # Render
    RES_X: int | None = None
    RES_Y: int | None = None
    RENDER_RES_X: int | None = None
    RENDER_RES_Y: int | None = None
    RENDER_PRESET: str = "default"
    RENDER_REQUEST_PATH: str = ""
    RENDER_REQUEST_SCHEMA: str = ""
    RENDER_REQUEST_SHA256: str = ""
    SAMPLES: int = 1
    ENGINE: str = "CYCLES"
    # Prefer an automatically discovered GPU; ``cycles_device`` falls back to
    # CPU when no usable device is available on the host.
    USE_GPU: bool = True
    CYCLES_DEVICE: str = ""
    CYCLES_DEVICE_NAME: str = ""
    USE_PERSISTENT_DATA: bool = True
    COMPOSITOR_DEVICE: str = "GPU"
    # Runtime-only record of the device actually assigned to Blender's
    # compositor.  The request still records the user's requested value.
    ACTUAL_COMPOSITOR_DEVICE: object = None
    RENDER_THREADS: int = 0
    CYCLES_USE_DENOISING: bool = False
    CYCLES_PIXEL_FILTER_TYPE: str = "BOX"
    CYCLES_FILTER_WIDTH: float = 0.01

    # Camera intrinsics
    f_mm: float | None = None
    CAMERA_PIXEL_PITCH_X_UM: float | None = None
    CAMERA_PIXEL_PITCH_Y_UM: float | None = None
    sensor_w: float | None = None
    sensor_h: float | None = None
    CAMERA_LENS_SHIFT_X: float | None = None
    CAMERA_LENS_SHIFT_Y: float | None = None
    CAMERA_INTRINSIC_MATRIX_PX: object = None
    CAMERA_NEAR_CLIP: float = 0.1

    # Rig topology
    CAMERA_TOPOLOGY: str | None = None

    # Projector (Projectors add-on)
    PROJECTOR_IMAGE: str = ""
    PROJECTOR_THROW_RATIO: float | None = None
    PROJECTOR_RES_X: int | None = None
    PROJECTOR_RES_Y: int | None = None
    PROJECTOR_PIXEL_PITCH_X_UM: float | None = None
    PROJECTOR_PIXEL_PITCH_Y_UM: float | None = None
    PROJECTOR_INTRINSIC_MATRIX_PX: object = None
    PROJECTOR_LENS_SHIFT_X: float | None = None
    PROJECTOR_LENS_SHIFT_Y: float | None = None
    PROJECTOR_ENERGY: float = 8.0
    # Fixed basis adapter for the vendored Projectors add-on.
    PROJECTOR_TEXTURE_MIRROR_X: bool = True
    PROJECTOR_TEXTURE_MIRROR_Y: bool = True
    PROJECTOR_TEXTURE_INTERPOLATION: str = "Linear"
    USE_PROJECTOR_MODEL: bool = False
    PROJECTOR_MODEL_PATH: str = "//assets/models/projector.obj"
    PROJECTOR_MODEL_SCALE: float = 1.0
    # Bundled OBJ meshes face -Y; X90 aligns them with the device's local -Z.
    PROJECTOR_MODEL_ROT_X_DEG: float = 90.0
    PROJECTOR_MODEL_ROT_Y_DEG: float = 0.0
    PROJECTOR_MODEL_ROT_Z_DEG: float = 0.0
    PROJECTOR_MODEL_DISABLE_RENDER: bool = False
    PROJECTOR_MODEL_DISABLE_SHADOWS: bool = True

    # Capture mode. "projector_sequence" preserves the original behavior.
    CAPTURE_MODE: str = "projector_sequence"
    PROJECTOR_CAPTURE_OUTPUT_DIR: str = "projector"
    PROJECTOR_CAPTURE_IMAGE_FORMAT: str = "BMP"

    # Output
    OUTPUT_DIR: str = "output"
    RENDER_FILE_FORMAT: str = "BMP"
    RENDER_COLOR_MODE: str = "BW"
    RENDER_FILM_TRANSPARENT: bool = False
    SAVE_RENDER_FLOAT_EXR: bool = False

    # Custom Target
    USE_CUSTOM_TARGET: bool = False
    CUSTOM_TARGET_PATH: str = ""
    # Box target size (meters)
    target_size_x: float = 0.2
    target_size_y: float = 0.2
    target_size_z: float = 0.02
    TARGET_CENTER_X: float = 0.0
    TARGET_CENTER_Y: float = 0.0
    TARGET_CENTER_Z: float = 0.0
    TARGET_SCALE: float = 1.0
    CUSTOM_TARGET_ROT_X_DEG: float = 0.0
    CUSTOM_TARGET_ROT_Y_DEG: float = 0.0
    CUSTOM_TARGET_ROT_Z_DEG: float = 0.0
    # Cylinder target size (meters)
    target_cyl_radius: float = 0.1
    target_cyl_height: float = 0.2
    target_cyl_axis: str = "y"
    TARGET_SHAPE: str = "box"

    # Material PBR
    MAT_ALBEDO: float = 0.7
    MAT_ROUGHNESS: float = 0.9
    MAT_METALLIC: float = 0.04
    MAT_SUBSURFACE: float = 0.0
    # True: read BRDF attributes from PLY (albedo/roughness/metallic). False: use config values.
    USE_PLY_BRDF: bool = False

    # Multiple bounce strength (Cycles)
    # Options: 'off', 'low', 'medium', 'high'
    BOUNCE_LEVEL: str = "off"

    # Flags
    NO_RENDER: bool = False
    METADATA_ONLY: bool = False

    # Environment light
    USE_ENV_LIGHT: bool = False
    ENV_LIGHT_STRENGTH: float = 20.0

    # Depth/point cloud output
    SAVE_DEPTH: bool = False
    SAVE_POINTCLOUD: bool = False

    # Camera model visualization
    USE_CAMERA_MODEL: bool = False
    CAMERA_MODEL_PATH: str = "//assets/models/camera.obj"
    CAMERA_MODEL_SCALE: float = 1.0
    CAMERA_MODEL_ROT_X_DEG: float = 90.0
    CAMERA_MODEL_ROT_Y_DEG: float = 0.0
    CAMERA_MODEL_ROT_Z_DEG: float = 0.0

    # Overview camera for publication/scene illustrations.
    USE_OVERVIEW_CAMERA: bool = False
    RENDER_OVERVIEW_ONLY: bool = False
    OVERVIEW_CAMERA_NAME: str = "overview_camera"
    OVERVIEW_CAMERA_LOCATION: tuple[float, float, float] = (0.65, -0.85, 0.35)
    OVERVIEW_CAMERA_LOOK_AT: tuple[float, float, float] = (-0.08, 0.0, -0.14)
    OVERVIEW_CAMERA_LENS_MM: float = 32.0
    OVERVIEW_CAMERA_SENSOR_WIDTH: float = 36.0

    APP_SCENE_CONFIG_PATH: str = ""
    APP_SCENE_CONFIG_SCHEMA: str = ""
    APP_SCENE_CONFIG_AUTHORITY: str = ""
    APP_SCENE_CONFIG_SHA256: str = ""
    APP_PROJECTOR_RES_X: int = 0
    APP_PROJECTOR_RES_Y: int = 0
    APP_MASK_THRESHOLD: int = 0
    APP_MASK_ACTIVE_PIXEL_COUNT: int = 0
    APP_MASK_ACTIVE_REGION_SHA256: str = ""
    APP_COMMON_VIEW_DEFINITION: str = ""
    APP_COMMON_VIEW_AREA_MM2: float = 0.0
    APP_COMMON_VIEW_REGION_SHA256: str = ""
    APP_RIG_METADATA: dict | None = None
    # Normalized camera records from derived.cameras. This is the authoritative
    # multi-camera source consumed by rig_topology; the singular APP_CAMERA_*
    # fields below are compatibility aliases for the right/primary camera.
    APP_CAMERA_DEVICES: tuple | None = None
    APP_CAMERA_CENTER_WORLD_M: tuple[float, float, float] | None = None
    APP_PROJECTOR_CENTER_WORLD_M: tuple[float, float, float] | None = None
    APP_CAMERA_DEVICE_TO_WORLD_MATRIX: tuple | None = None
    APP_PROJECTOR_DEVICE_TO_WORLD_MATRIX: tuple | None = None
    APP_CAMERA_PROJECTION_MATRIX_PX: tuple | None = None
    APP_PROJECTOR_PROJECTION_MATRIX_PX: tuple | None = None

    def normalized_camera_topology(self):
        raw = getattr(self, "CAMERA_TOPOLOGY", None)
        if raw is None:
            raise ValueError(
                "CAMERA_TOPOLOGY is unavailable before App scene injection"
            )
        topology = str(raw).strip().lower()
        aliases = {
            "stereo": "dual",
            "dual-camera": "dual",
            "dual_camera": "dual",
            "single-camera": "single",
            "single_camera": "single",
        }
        topology = aliases.get(topology, topology)
        if topology not in {"dual", "single"}:
            raise ValueError(f"Unsupported CAMERA_TOPOLOGY: {self.CAMERA_TOPOLOGY}")
        return topology
