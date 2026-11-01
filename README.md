# DMD 结构光仿真环境

**简体中文** | [English](README.en.md)

基于 Blender/Cycles 的单相机、单投影仪结构光仿真工具，支持**正交 DMD**和
**Diamond-DMD（DLP4500）**。通过 JSON 配置设备、图案、目标和材质，输出相机图像、
浮点图像、深度、点云及标定参数。

## 安装与运行

已验证环境：Linux、Python 3.10、Blender 4.5.3。先单独安装 Blender，
再安装本仓库唯一的 Python 依赖文件：

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

两个示例自动生成平面目标和四步相移正弦图案，采用 CPU 渲染，输出 320×240 相机图像。
每次运行请指定一个新输出目录；已有目录不会被覆盖。

| 正交 DMD 示例 | Diamond-DMD 示例 |
|---|---|
| ![正交 DMD 渲染](docs/images/orthogonal.png) | ![Diamond-DMD 渲染](docs/images/diamond.png) |

## 两种 DMD

| 类型 | 配置中的逻辑分辨率 | 仿真投影纹理 |
|---|---|---|
| `orthogonal` | 宽、高均可配置 | 与逻辑图像相同 |
| `diamond` | DLP4500：912×1140 | 逐像素映射到 1481×1482 载体，并匹配投影仪旋转 |

Diamond 的载体尺寸用于仿真纹理，不是设备上传尺寸。坐标映射保存在
`inputs/dmd_mapping.json`；导出的投影仪内参对应载体坐标。
当前采用几何投影与纹理成像模型，不包含微镜时序、衍射或实测光学 PSF。

## 修改配置

复制一个示例的 `scene.json`，按需修改：

- `camera`、`projector`：位置、朝向、分辨率、焦距、像元尺寸。
- `patterns`：自动生成正弦图案，或提供按采集顺序排列的灰度图文件列表。
- `target`：平面、方盒、圆柱或自定义模型，以及目标材质。
- `render`、`capture`：采样数、CPU/GPU、投影亮度及输出类型。

尺寸使用 `[宽, 高]`；位置单位为米，焦距为毫米，像元尺寸为微米。
在运行命令后增加 `--prepare-only` 可只生成输入；增加 `--validate-only` 可在
Blender 中建立场景并检查几何，不执行采集。完整字段见[配置说明](docs/configuration.md)。

## 输出与目录

运行结果保存在指定的输出目录中：

- `inputs/`：配置副本、图案、坐标映射和渲染请求。
- 渲染子目录：相机图像、可选的浮点 EXR、深度、世界坐标 PLY 点云，
  以及 `calibration.json`、`params.txt` 和 `render_manifest.json`。
- `run.json`：运行状态；`COMPLETE` 表示完成渲染，`VALIDATED` 表示完成场景校验。

仓库的主要目录如下：

```text
examples/     两种 DMD 的独立配置
scripts/      运行、验证和后处理入口
src/          配置、坐标、投影和渲染实现
blender/      Blender 场景执行入口
tests/        自动测试
docs/         配置、坐标与验证说明
3rdparty/     投影仪插件
```

## 验证

安装 `requirements.txt` 后即可运行以下检查：

```bash
python -m unittest discover -s tests -v
python scripts/validate_examples.py --output output/validation
"$BLENDER_BIN" -b --factory-startup -t 2 --python-exit-code 1 --python tests/blender_pixel_coordinates.py
```

示例检查包括相移图像、辅助输出、标定文件、完成清单和点云几何。
点云由深度真值生成；这些检查用于确认功能和坐标一致性。
详见[验证记录](docs/validation.md)、[DMD 坐标](docs/dmd_coordinates.md)、
[像素与标定坐标](docs/pixel_coordinates.md)和[标定板及后处理接口](docs/advanced.md)。

## 许可证

项目代码采用 **GPL-3.0-or-later**。Copyright © 2026 Jia Zhizhou and contributors。
第三方代码保留原作者及许可证声明，详见 [LICENSE](LICENSE) 和
[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。
