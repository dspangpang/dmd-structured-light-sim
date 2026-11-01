# 正交 DMD 示例

按[首页说明](../../README.md)安装依赖并设置 Blender 路径后，从仓库根目录运行：

```bash
python scripts/run_simulation.py --config examples/orthogonal/scene.json --output output/orthogonal
```

示例生成四张 640×480 正弦相移图案，直接作为投影纹理照射平面目标。
相机输出 320×240 图像、浮点 EXR、深度和世界坐标 PLY 点云。
所有目标和图案均由程序生成，每次运行使用新的输出目录。
