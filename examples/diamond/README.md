# Diamond-DMD 球体示例

按[首页说明](../../README.md)安装依赖并设置 Blender 路径后，从仓库根目录运行：

```bash
python scripts/run_simulation.py --config examples/diamond/scene.json --output output/diamond
```

示例加载自带的球体、相机与投影仪模型，生成周期 48 像素的四步相移条纹。
默认逻辑阵列为 640×480；修改 `scene.json` 的 `projector.resolution` 可配置其他
宽高，包括奇数行列和 DLP4500 的 912×1140。仿真载体和标定真值随尺寸自动生成。
相机输出四张 512×512 图像、浮点 EXR、深度和世界坐标 PLY 点云。
每次运行使用新的输出目录。模型和参数的含义见首页。
