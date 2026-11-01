# Diamond-DMD 示例（DLP4500）

按[首页说明](../../README.md)安装依赖并设置 Blender 路径后，从仓库根目录运行：

```bash
python scripts/run_simulation.py --config examples/diamond/scene.json --output output/diamond
```

示例将四张 912×1140 逻辑正弦图案逐像素映射到 1481×1482 载体纹理，并设置配套的
−45° 投影仪旋转。输入图案和映射关系保存在输出目录中；相机输出 320×240 图像、
浮点 EXR、深度和世界坐标 PLY 点云。每次运行使用新的输出目录。
