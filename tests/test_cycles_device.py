import sys
import unittest
from pathlib import Path
from types import SimpleNamespace as NS
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from cycles_device import configure_cycles_device, resolve_compositor_device


class Preferences:
    def __init__(self, available):
        self.available = available
        self.devices = []
        self.compute_device_type = 'NONE'

    def get_devices(self):
        value = self.available.get(self.compute_device_type, RuntimeError('unsupported backend'))
        if isinstance(value, Exception):
            raise value
        self.devices = [NS(name=name, type=self.compute_device_type, use=False) for name in value]
        self.devices.append(NS(name='CPU', type='CPU', use=True))


class DeviceTests(unittest.TestCase):
    def test_gpu_compositor_uses_cpu_in_background_mode(self):
        actual, reason = resolve_compositor_device(
            'GPU', background=True, display_available=True, gpu_enabled=True
        )
        self.assertEqual(actual, 'CPU')
        self.assertIn('background mode', reason)

    def test_gpu_compositor_uses_cpu_without_display(self):
        actual, reason = resolve_compositor_device(
            'GPU', background=False, display_available=False, gpu_enabled=True
        )
        self.assertEqual(actual, 'CPU')
        self.assertIn('no graphical display', reason)

    def test_gpu_compositor_remains_available_for_interactive_gpu_render(self):
        actual, reason = resolve_compositor_device(
            'GPU', background=False, display_available=True, gpu_enabled=True
        )
        self.assertEqual((actual, reason), ('GPU', None))

    def test_cpu_compositor_is_selected_when_gpu_render_is_disabled(self):
        actual, reason = resolve_compositor_device(
            'GPU', background=False, display_available=True, gpu_enabled=False
        )
        self.assertEqual(actual, 'CPU')
        self.assertIn('disabled', reason)

    def test_unknown_compositor_device_is_rejected(self):
        with self.assertRaises(ValueError):
            resolve_compositor_device('CUDA')

    def select(self, available, gpu=True, backend='', name=''):
        scene = NS(cycles=NS(device='CPU'), render=NS(compositor_device='GPU'))
        cfg = NS(USE_GPU=gpu, CYCLES_DEVICE=backend, CYCLES_DEVICE_NAME=name)
        prefs = Preferences(available) if available is not None else None
        result = configure_cycles_device(scene, prefs, cfg)
        self.assertEqual(scene.cycles.device, result['device'])
        self.assertEqual(cfg.ACTUAL_CYCLES_DEVICE, result)
        return result, prefs, scene

    def test_auto_skips_unavailable_backend_and_excludes_cpu(self):
        result, prefs, _ = self.select({'CUDA': ['GPU A', 'GPU B']})
        self.assertEqual(result['backend'], 'CUDA')
        self.assertEqual(result['devices'], ['GPU A', 'GPU B'])
        self.assertFalse(prefs.devices[-1].use)

    def test_cpu_only_machine_and_missing_preferences(self):
        for available in ({}, {'OPTIX': []}, None):
            result, _, scene = self.select(available)
            self.assertEqual(result['device'], 'CPU')
            self.assertTrue(result['fallback_reason'])
            self.assertEqual(scene.render.compositor_device, 'CPU')

    def test_explicit_cpu_skips_discovery(self):
        result, prefs, scene = self.select({}, gpu=False)
        self.assertEqual(prefs.compute_device_type, 'NONE')
        self.assertIsNone(result['fallback_reason'])
        self.assertEqual(scene.render.compositor_device, 'CPU')

    def test_explicit_backend_and_name(self):
        result, _, _ = self.select({'CUDA': ['GPU A', 'GPU B']}, backend='CUDA', name='gpu b')
        self.assertEqual(result['devices'], ['GPU B'])

    def test_filter_searches_later_backends(self):
        result, _, _ = self.select({'OPTIX': ['GPU A'], 'HIP': ['GPU B']}, name='GPU B')
        self.assertEqual(result['backend'], 'HIP')

    def test_explicit_unavailable_backend_does_not_use_stale_backend(self):
        result, _, _ = self.select({'CUDA': ['GPU A']}, backend='OPTIX')
        self.assertEqual(result['device'], 'CPU')

    def test_unmatched_filter_falls_back(self):
        result, _, _ = self.select({'CUDA': ['GPU A']}, name='missing')
        self.assertEqual(result['device'], 'CPU')

    def test_typo_is_configuration_error(self):
        with self.assertRaises(ValueError):
            self.select({}, backend='CUDDA')

if __name__ == '__main__':
    unittest.main()
