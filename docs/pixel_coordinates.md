# 像素与标定坐标

导入和导出的 K/P 矩阵均采用 OpenCV 像素坐标：(0, 0) 表示第一个像素的中心，
u 向右、v 向下，图像范围为 `[-0.5, W-0.5] × [-0.5, H-0.5]`。
Blender 的 NDC/纹理 UV 对应图像边界，换算为 `u=W*s-0.5`、`v=H*(1-t)-0.5`。
`src/pixel_coordinates.py` 在 Blender 接口处执行该换算和镜头偏移适配。
导出的内参、深度数组和图像无需再次添加半像素偏移。

## 标定文件

`calibration.json` 使用 `dmd-sim.calibration.v1` 格式，平移单位为米，设备坐标轴为
X 向右、Y 向下、Z 向前。主要字段为：

| 字段 | 内容 |
|---|---|
| `devices` | 各相机和投影仪的尺寸、K、畸变、世界到设备变换及 P |
| `reference_camera` | 参考相机名称 |
| `device_transforms` | 相机到其他设备的相对变换，满足 `X_target = R X_source + t` |
| `scene_config_sha256` | 本次场景配置的校验值 |
| `projector_coordinate_domain` | 投影仪矩阵所描述的纹理坐标域 |

矩阵保留 JSON 浮点精度。Diamond 投影仪的矩阵对应载体纹理，逻辑/原生坐标需通过
`inputs/dmd_mapping.json` 转换。`params.txt` 记录供人阅读的 Blender 对象矩阵、
镜头和纹理状态；程序应读取 `calibration.json`。

## 读写接口

`src/calibration.py` 提供 `save_calibration(cfg, output_dir, projector_resolution)`，
可用 `parameters=` 传入显式设备参数。
`src/calibration_io.py` 提供 `read_calibration(path)`，仅依赖 Python 标准库，
负责验证矩阵、单位、内外参一致性和相机/投影仪配对。返回的 `devices` 中矩阵为
普通二维列表；多投影仪数据需通过 `camera_projectors` 显式指定配对。

真实 Blender 投影和 Cycles 像素检查可从仓库根目录运行：

```bash
"$BLENDER_BIN" -b --factory-startup -t 2 --python-exit-code 1 --python tests/blender_pixel_coordinates.py
```
