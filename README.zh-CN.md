# DMD 结构光仿真环境

基于 Blender/Cycles 的单相机、单投影仪仿真环境，支持**正交 DMD**与
**Diamond-DMD（DLP4500）**。可以配置相机与投影仪、输入投影图案、选择目标与材质，
输出相机图像、浮点图像、深度、世界坐标点云及几何参数。

## 安装和运行

当前验证环境为 Linux、Blender 4.5.3、Python 3.10。Blender 单独安装。
示例不需要 gwCamSDK、NAS 数据、GUI 配置程序或外部模型。

```bash
git clone https://github.com/dspangpang/dmd-structured-light-sim.git
cd dmd-structured-light-sim
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
export BLENDER_BIN=/你的/Blender/可执行文件

python scripts/run_simulation.py --config examples/orthogonal/scene.json --output output/orthogonal
python scripts/run_simulation.py --config examples/diamond/scene.json --output output/diamond
```

两个示例都自动生成平面目标和四步相移正弦图案，使用 CPU、320×240 相机分辨率和
每像素 8 次采样。它们用于验证流程，不构成精度对比实验。

输出目录必须是新目录，已有实验不会被覆盖。`--prepare-only` 仅准备输入；
`--validate-only` 在 Blender 中建立场景并验证几何，不执行正式采集。

## 两种 DMD

- 正交 DMD：逻辑图像与投影纹理采用相同尺寸的正交网格，分辨率可配置。
- Diamond-DMD：首版明确支持 DLP4500 的 912×1140 逻辑输入。程序逐像素映射到
  1481×1482 的仿真载体纹理，并配套设置投影仪旋转。载体尺寸不是设备上传分辨率。

Diamond 的逻辑、原生和载体坐标关系写入 `inputs/dmd_mapping.json`。
导出的投影仪内参描述载体坐标；使用原生 Diamond 坐标时需应用对应映射。
详见[坐标说明](docs/dmd_coordinates.md)。当前模型采用几何投影与纹理成像，
不包含微镜开关时序、衍射或实测光学 PSF。

## 修改配置与验证

复制相应 `scene.json` 后修改位置、朝向、焦距、像元尺寸、图案、目标或渲染参数。
尺寸使用 `[宽, 高]`，位置单位为米，焦距为毫米，像元尺寸为微米。
自定义图案使用显式文件列表，处理顺序与列表一致。
详见[配置说明](docs/configuration.md)。

```bash
python -m pip install -r requirements-dev.txt
python -m unittest discover -s tests -v
python scripts/validate_examples.py --output output/validation
"$BLENDER_BIN" -b --factory-startup -t 2 --python-exit-code 1 --python tests/blender_pixel_coordinates.py
```

每次运行保留输入、映射、标定参数和运行清单。`COMPLETE` 表示完成真实渲染；
`VALIDATED` 仅表示场景建立与校验完成。可选的 SDK 重建接口与标定板入口见
[高级接口](docs/advanced.md)，不属于运行两个示例的前置依赖。

项目采用 **GPL-3.0-or-later**，第三方声明与来源分别见
[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) 和 [SOURCE_PROVENANCE.md](SOURCE_PROVENANCE.md)。
