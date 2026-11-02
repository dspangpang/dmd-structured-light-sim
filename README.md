# DMD 结构光仿真环境

**简体中文** | [English](README.en.md)

基于 Blender/Cycles 的单相机、单投影仪结构光仿真，支持**正交 DMD**和
**可配置行列数的 Diamond-DMD**。

投影仪建模与纹理投影使用 **Jonas Schell** 开发的
**[Projectors](https://github.com/Ocupe/Projectors)** Blender 插件（GPL-3.0-or-later）。
本项目在此基础上提供两类 DMD 的适配、条纹序列仿真和标定真值导出。
仓库内置了适配后的插件，运行脚本会自动加载，无需另行安装。
插件修改记录见 [UPSTREAM.md](3rdparty/Projectors/UPSTREAM.md)，
版权与许可证说明见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。

## Blender 界面与四步相移示例

![Blender 窗口中的设备布置、球面条纹和四步相移时间轴](assets/blender-ui.gif)

上图直接截取运行中的 Blender 窗口：左侧为相机、投影仪和球体的整体场景，
右侧为相机视图，时间轴第 1–4 帧对应 0°、90°、180°、270° 相位。
界面演示加载本地厂商 CAD（me2p_v1226 相机、LightCrafter4500 投影仪）；
这些 CAD 源文件不随仓库分发，默认示例使用自带的轻量外壳模型。

球体半径为 0.1 米，条纹周期为 48 个逻辑像素。两种 DMD 示例使用相同的球体
和四步相移设置。[相机输出动图](assets/preview.gif)保留四张 512×512 渲染图的灰度值；
Blender 视口使用 Standard 显示变换，供交互观察。

## 安装与运行

已验证环境：Linux、Python 3.10、Blender 4.5.3。先安装 Blender，再执行：

```bash
git clone https://github.com/dspangpang/dmd-structured-light-sim.git
cd dmd-structured-light-sim
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
export BLENDER_BIN=/path/to/blender

# 正交 DMD
python scripts/run_simulation.py --config examples/orthogonal/scene.json --output output/orthogonal

# Diamond-DMD
python scripts/run_simulation.py --config examples/diamond/scene.json --output output/diamond
```

示例直接加载仓库自带模型并生成条纹，默认使用 CPU。每次运行请指定新输出目录。

打开可交互场景：

```bash
python scripts/run_simulation.py --config examples/orthogonal/scene.json --output output/interactive --interactive
```

在 Blender 时间轴切换第 1–4 帧即可观察相移，也可按空格播放。
中键拖动左侧视图可旋转观察设备，右侧保持相机视角。
场景会保存为 `output/interactive/scene.blend`，四张条纹已内嵌，可直接重新打开。
将配置换成 `examples/diamond/scene.json` 即可查看 Diamond-DMD。

## 配置与模型

复制一个示例的 `scene.json` 即可修改设备位置、朝向、焦距、分辨率、图案、目标和材质。
`patterns.period_px` 控制周期，`phase_steps` 控制相移步数；自定义灰度图按 `patterns.files`
的显式列表顺序输入。位置单位为米，焦距为毫米，像元尺寸为微米，分辨率为 `[宽, 高]`。

| DMD 类型 | 逻辑输入 | 仿真纹理 |
|---|---|---|
| `orthogonal` | 宽、高可配置 | 与输入相同 |
| `diamond` | 宽、高可配置，支持奇数行列 | 根据输入尺寸自动生成 45° 错列阵列载体，并匹配投影仪旋转 |

Diamond 示例默认使用 `projector.resolution: [640, 480]`。修改这两个数即可配置
其他尺寸，宽、高均为至少 2 的整数；例如 `[641, 481]`、`[1280, 800]`，
也支持 DLP4500 的 `[912, 1140]`。自定义条纹图像必须与配置尺寸一致。
载体尺寸、坐标映射和导出的投影仪内参会自动更新。

Diamond 的载体尺寸不是硬件上传尺寸，导出的投影仪内参对应载体坐标，坐标换算见
运行结果中的 `inputs/dmd_mapping.json`。当前模型描述几何投影与纹理成像。

`assets/models/` 提供三个以米为单位的 OBJ 模型：

- `camera.obj`、`projector.obj`：轻量设备外壳示意，由 `visualization` 配置加载；光学参数由配置决定。
- `sphere.obj`：半径 0.1 米、中心在原点的球体，由 `target.asset` 加载。

模型是本项目生成的原创示例，可通过 Blender 执行 `scripts/generate_assets.py` 重新生成。
材质文件与对应 OBJ 放在同一目录。所有安装与测试依赖都在唯一的 `requirements.txt` 中。
使用自己的设备 CAD 时，将 `visualization.camera_model.path` 和
`visualization.projector_model.path` 指向本地 OBJ，并设置各自的 `rotation_deg`。
自带外壳使用子对象局部旋转 `[90, 0, 0]`，将 OBJ 的前向轴对齐设备光轴；
该设置只控制外壳朝向，光学设备的姿态仍由 `position_m`、`look_at_m` 和 `up` 决定。

## 最小竖条纹 Gray 码与三维重建

两个 Python 脚本提供“四步相移 + Gray 级次码 + 一张互补码”的最小示例，
只支持竖条纹。Gray 位数由分辨率和周期自动确定。

```bash
# 1. 生成条纹、帧顺序和场景配置
python scripts/generate_gray.py --config examples/orthogonal/scene.json --period 48 --output output/gray-input
# 2. 用 Blender 渲染相机图像
python scripts/run_simulation.py --config output/gray-input/scene.json --output output/gray-render
# 3. 解码并使用标定真值进行三角测量
python scripts/reconstruct_gray.py --run output/gray-render --sequence output/gray-input/sequence.json --output output/gray-reconstruction
```

第一步改用 `examples/diamond/scene.json` 即可运行 Diamond-DMD；每次使用新输出目录。
这里 `--period` 至少为 8，以原生水平坐标 `U` 为单位：正交阵列为列号，
Diamond 为 `U = 2 × 列号 + 行号奇偶性`，相邻逻辑列相隔 2 个单位。
解码后结合 `calibration.json` 和 `dmd_mapping.json`，求相机射线与投影条纹平面的交点。
输出 `points.ply` 和 `reconstruction.npz`（原生 `U`、世界坐标 XYZ、有效掩膜），长度单位为米。
渲染目录中直接导出的深度和点云属于真值，可用于对照重建结果。
该序列通过批量渲染运行；`--interactive` 演示仍使用前面的四张正弦条纹。

## 输出与验证

`inputs/` 保存配置、条纹和映射；渲染子目录保存相机图像、浮点 EXR、深度、PLY 点云、
`calibration.json` 和运行清单。

`calibration.json` 直接从场景导出**标定真值**：相机和投影仪内参 `K`、
世界到设备的外参 `world_to_device`、投影矩阵 `P` 和设备间相对变换 `device_transforms`。
采用 OpenCV 像素中心坐标，平移单位为米，理想模型的畸变系数为零。
仓库只提供真值导出，不包含标定板生成、标定图像采集或参数估计流程。

`run.json` 中的 `COMPLETE` 表示渲染完成；`--prepare-only` 仅准备输入，
`--validate-only` 仅建立场景并校验几何。点云由深度真值生成。

```bash
# 单元测试与两种 DMD 的实际渲染检查，包含奇数行列的 Diamond 阵列
python -m unittest discover -s tests -v
python scripts/validate_examples.py --output output/validation

# 用已渲染的四张相移图生成动图，输出文件须不存在
python scripts/make_preview.py --run output/orthogonal --output output/sphere.gif

# 无窗口生成可打开的 Blender 场景，输出文件须不存在
"$BLENDER_BIN" -b --factory-startup --python-exit-code 1 --python blender/open_scene.py -- --render-request output/orthogonal/inputs/render_request.json --save output/scene.blend
```

## 许可证

项目代码和自带示例模型采用 **GPL-3.0-or-later**。
Copyright © 2026 Jia Zhizhou and contributors。
第三方声明见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)，完整许可证见 [LICENSE](LICENSE)。
