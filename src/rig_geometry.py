"""Apply authoritative device poses exported by the current App scene contract."""


# Blender camera/projector objects look along local -Z with local +Y upward.
# This fixed adapter only converts Blender's object basis to the device basis;
# all scene geometry itself comes from the App matrix.
_BLENDER_OBJECT_TO_DEVICE = (
    (1.0, 0.0, 0.0),
    (0.0, -1.0, 0.0),
    (0.0, 0.0, -1.0),
)


def _required_vector(cfg, attribute, label):
    value = getattr(cfg, attribute, None)
    if value is None or len(value) != 3:
        raise ValueError(f"App scene is missing {label}")
    return tuple(float(component) for component in value)


def _required_matrix(cfg, attribute, label):
    value = getattr(cfg, attribute, None)
    if value is None or len(value) != 3 or any(len(row) != 3 for row in value):
        raise ValueError(f"App scene is missing {label}")
    return tuple(tuple(float(component) for component in row) for row in value)


def _matrix_multiply(a, b):
    return tuple(
        tuple(sum(a[row][k] * b[k][col] for k in range(3)) for col in range(3))
        for row in range(3)
    )


def camera_devices(cfg):
    devices = getattr(cfg, "APP_CAMERA_DEVICES", None)
    if not isinstance(devices, (tuple, list)) or not devices:
        raise ValueError("App scene is missing normalized derived.cameras records")
    return tuple(devices)


def _camera_device(cfg, device=None):
    if device is not None:
        return device
    devices = camera_devices(cfg)
    return next(
        (item for item in devices if item.get("source_key") == "right"),
        devices[0],
    )


def camera_local_position(cfg, device=None):
    record = _camera_device(cfg, device)
    camera_world = record.get("center_world_m")
    if camera_world is None or len(camera_world) != 3:
        raise ValueError("App camera record is missing center_world_m")
    projector_world = _required_vector(
        cfg, "APP_PROJECTOR_CENTER_WORLD_M", "projector center_world_mm"
    )
    return tuple(camera_world[index] - projector_world[index] for index in range(3))


def camera_device_to_world_matrix(cfg, device=None):
    record = _camera_device(cfg, device)
    value = record.get("device_to_world_3x3")
    if value is None or len(value) != 3 or any(len(row) != 3 for row in value):
        raise ValueError("App camera record is missing device_to_world_3x3")
    return tuple(tuple(float(component) for component in row) for row in value)


def camera_local_rotation_matrix(cfg, device=None):
    return _matrix_multiply(
        camera_device_to_world_matrix(cfg, device),
        _BLENDER_OBJECT_TO_DEVICE,
    )


def projector_device_to_world_matrix(cfg):
    return _required_matrix(
        cfg,
        "APP_PROJECTOR_DEVICE_TO_WORLD_MATRIX",
        "projector device_to_world_3x3",
    )


def projector_world_position(cfg):
    return _required_vector(
        cfg, "APP_PROJECTOR_CENTER_WORLD_M", "projector center_world_mm"
    )


def projector_local_rotation_matrix(cfg):
    return _matrix_multiply(
        projector_device_to_world_matrix(cfg),
        _BLENDER_OBJECT_TO_DEVICE,
    )
