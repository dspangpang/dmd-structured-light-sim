# 公共投影仪模糊配置

所有维护中的仿真分支共用 `src/projector_psf.py`。新建运行默认使用 Gaussian，
sigma_px=1、subsamples_per_pixel=32、constant 零边界。请在请求的 `render.projector_psf`
中记录完整设置；sigma_px=0 是无光学模糊的对照。FoSSL-led 的周期条纹请求显式选择 repeat。

原始图像保持 Closest 像素单元与原始物理分辨率，连续卷积后接入投影仪 Emission，
再参与表面反射和相机成像。二维纹理使用可分离 CDF；repeat 下严格沿行或列不变的
条纹使用数学等价的一维 CDF，避免展开为大尺寸二维表。数值表的 Linear 仅用于连续场查值。

sigma_unit=source_pixel 表示输入纹理像素；Diamond 的输入纹理为 carrier 网格，可显式写
carrier_pixel。它不等于 Diamond 原生 U/V 微镜间距，不能直接混用数值。

每次采集保存 projector_psf_updates.jsonl，包含投影仪、源图哈希、实际着色连接、数值后端、
物理分辨率和实现哈希。实现与设置进入运行身份，避免复用旧模型的渲染。
旧请求缺省时也采用此默认值；复现实验必须显式记录 sigma_px=0 或原实验配置。

相机自身的 PSF 可独立保留在相机后处理。禁止把投影仪 sigma 按面积比折算后加到相机图上，
也不能用该后处理替代投影端的光学卷积。公共修改在 core-sim 维护，再向各项目同步。
