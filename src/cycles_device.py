"""Portable Cycles and compositor device selection.

Cycles can render with a GPU from Blender's background mode.  The GPU
compositor is different: on a headless host Blender may not have a current
GLX/EGL context, and assigning ``scene.render.compositor_device = 'GPU'`` can
abort inside Blender's OpenGL loader before Python gets an exception.  Keep
that decision explicit so background jobs safely use the CPU compositor while
Cycles still discovers and uses a GPU.
"""
import json

BACKENDS = ('OPTIX', 'CUDA', 'HIP', 'ONEAPI', 'METAL')


def resolve_compositor_device(
    requested,
    *,
    background=False,
    display_available=True,
    gpu_enabled=True,
):
    """Return a safe compositor device and an optional fallback reason.

    Blender's GPU compositor needs a graphical context even when Cycles can
    run headlessly.  Background jobs therefore use the CPU compositor by
    default.  A missing display or an explicit CPU render request also forces
    the CPU compositor.  The requested value remains in the render request;
    callers can record the returned device as the actual runtime choice.
    """

    normalized = str(requested or "CPU").strip().upper()
    if normalized not in {"CPU", "GPU"}:
        raise ValueError(
            f"Unknown compositor device: {normalized}; expected CPU or GPU"
        )
    if normalized == "CPU":
        return "CPU", None
    if not gpu_enabled:
        return "CPU", "GPU rendering was disabled"
    if background:
        return "CPU", "Blender is running in background mode without a guaranteed GLX/EGL context"
    if not display_available:
        return "CPU", "no graphical display is available for the GPU compositor"
    return "GPU", None


def configure_cycles_device(scene, preferences, cfg):
    requested = str(getattr(cfg, 'CYCLES_DEVICE', '') or '').strip().upper()
    filters = [part.strip().upper() for part in str(getattr(cfg, 'CYCLES_DEVICE_NAME', '') or '').split(',') if part.strip()]
    report = dict(requested_gpu=bool(cfg.USE_GPU), requested_backend=requested,
                  name_filters=filters, device='CPU', backend='CPU', devices=[], attempts=[])

    def finish(reason=None):
        report['fallback_reason'] = reason
        scene.cycles.device = report['device']
        if report['device'] == 'CPU' and hasattr(scene.render, 'compositor_device'):
            scene.render.compositor_device = 'CPU'
        compositor_record = getattr(cfg, 'ACTUAL_COMPOSITOR_DEVICE', None)
        if report['device'] == 'CPU':
            if isinstance(compositor_record, dict):
                compositor_record = dict(compositor_record)
                if compositor_record.get('device') == 'GPU':
                    compositor_record['device'] = 'CPU'
                    compositor_record['fallback_reason'] = (
                        compositor_record.get('fallback_reason')
                        or reason
                        or 'Cycles GPU was unavailable'
                    )
            else:
                compositor_record = {
                    'requested': str(getattr(cfg, 'COMPOSITOR_DEVICE', 'CPU') or 'CPU').upper(),
                    'device': 'CPU',
                    'fallback_reason': reason or 'Cycles GPU was unavailable',
                }
            cfg.ACTUAL_COMPOSITOR_DEVICE = compositor_record
        report['compositor_device'] = getattr(scene.render, 'compositor_device', None)
        cfg.ACTUAL_CYCLES_DEVICE = report
        print('[Render] Cycles device selection: ' + json.dumps(report))
        if reason:
            print('[Render] GPU unavailable; falling back to CPU: ' + reason)
        return report

    if not cfg.USE_GPU:
        return finish()
    if requested and requested not in BACKENDS:
        raise ValueError(f'Unknown Cycles backend: {requested}; expected one of {BACKENDS} or empty for automatic')
    if preferences is None:
        return finish('Cycles device preferences are unavailable')
    for backend in ((requested,) if requested else BACKENDS):
        try:
            preferences.compute_device_type = backend
            preferences.get_devices()
            enabled = []
            for device in preferences.devices:
                name = str(device.name)
                matches = not filters or any(part in name.upper() for part in filters)
                device.use = str(device.type).upper() == backend and matches
                if device.use:
                    enabled.append(name)
            if not enabled:
                report['attempts'].append(dict(backend=backend, error='No matching GPU devices'))
                continue
            scene.cycles.device = 'GPU'
        except Exception as exc:
            report['attempts'].append(dict(backend=backend, error=f'{type(exc).__name__}: {exc}'))
            continue
        report.update(device='GPU', backend=backend, devices=enabled)
        return finish()
    return finish('No usable GPU matched the requested backend/device selection')
