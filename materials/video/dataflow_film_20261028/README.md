# 一帧图像的数据旅程 · 第一版

100 秒、7 个章节的程序化数据流影片。画面采用石墨黑、青色图像流、淡紫色模型流；包含缓动推近、横移跟随、轻微镜头旋转、拉远总览、源帧卡片、ROI 裁出、传输光点、DDR 队列、NPU 网格和训练回路。音轨由代码合成，无人声。

## 观看

- `output/dataflow-v1-4k60.mp4`：3840 × 2160、60 FPS 母版。
- `output/dataflow-v1-preview-1080p60.mp4`：便于预览的 1920 × 1080、60 FPS 版本。
- `preview.html`：直接用浏览器打开即可。支持播放、暂停、拖动时间轴、章节跳转、声音开关和全屏。与 `renderer.js`、`soundtrack.m4a` 放在同一目录。

MP4 按固定时间步长逐帧渲染，与浏览器播放性能无关。浏览器预览实际刷新速度取决于观看设备；不宣称网页预览一定达到 60 FPS。

## 分镜

| 时段 | 内容 | 运镜 |
| --- | --- | --- |
| 00:00–00:09 | 让图像成为判断 | 图像层叠、轨道粒子、缓慢拉远与轻旋转 |
| 00:09–00:24 | IMX415、ISP、增强、前景、边缘与形态学 | 沿处理链横移，最后拉远 |
| 00:24–00:41 | 预览、推理 ROI、按需采图三路与 DDR / PCIe | 从分流点推移到三路全景 |
| 00:41–00:54 | 原生裁剪、NPU、位置 / 姿态 / 置信度输出 | 跟随 ROI 裁片向右移动 |
| 00:54–01:06 | 跨帧 ID、姿态确认、计数、显示、GPIO、记录 | 沿时间序列横移至输出端 |
| 01:06–01:27 | 15 张采图、标注、GPU 训练、10 张新图测试、转换、明确选型和部署 | 扫过流程上半部，再拉远看完整回路 |
| 01:27–01:40 | 相机、FPGA、RK3576、PC 与控制回路 | 轻旋转归正，拉远全景 |

## 内容依据与边界

依据本项目的以下本地文件整理，未连接板卡或加载模型：

- `../three_stream_20261028/README.md`：公共处理和三路离线候选、DDR / PCIe、预览和两路原生 RGB 的边界。
- `../three_stream_20261028/host/ABI.md`：数据身份和传输协议。
- `../three_stream_20261028/runtime/full_roi_runtime.py`、`rect_pipeline.py`：ROI 推理、跟踪和结果发布。
- `../VisionTrainer/README.md`、`console_worker.py`、`console_workflow.py`、`console_model_deploy.py`：四步流程、原生裁剪、GPU 训练、测试、转换与模型专用部署。

三路方案是离线候选，尚未完成当前组合的实板验收。影片中的图像、检测框、源帧编号、队列、计数、GPIO 波形及节奏均为流程示意，不代表实拍、模型识别结果或实测性能。4K / 60 FPS 是影片输出规格。原生裁片在动画里等比放大用于观看，不执行任何项目图像处理。

影片概括模块级数据流；GPIO、识别、计数与设备显示帧率仍以当前组合的现场证据为准。没有使用任何历史模型权重、改动板端服务、修改训练原图或改变现场参数。

## 源码

- `renderer.js`：浏览器和离线渲染共用的确定性 Canvas 画面与运镜。
- `render.cjs`：固定帧步长渲染，RGBA 管道交给 FFmpeg 编码。
- `audio.py`：原创氛围音轨与转场音效，随机种子固定。
- `preview.html`：独立网页播放器。
- `verify-preview.cjs`：浏览器交互和章节边界检查。
- `output/preview-verification.json`、`output/media-verification.json`：预览与成片验证记录。

依赖：Node.js、FFmpeg；离线渲染使用 `@napi-rs/canvas`，浏览器检查使用 Playwright。音乐生成另需 Python 与 NumPy。Windows 系统字体使用 Microsoft YaHei、Consolas 和 Bahnschrift；未复制或分发字体文件。

```powershell
npm ci
python audio.py
ffmpeg -y -i soundtrack.wav -c:a aac -b:a 192k soundtrack.m4a
node render.cjs --width 3840 --fps 60 --output dataflow-v1-4k60.mp4
node verify-preview.cjs
```

默认使用 CPU 的 libx264 编码。制作本版本时，本机默认 FFmpeg 9.0.2 要求的 NVENC API 高于当前驱动，故仅在此目录安装 imageio-ffmpeg 0.6.0 的独立 FFmpeg 7.1 二进制，以兼容现有驱动；未更新系统驱动或覆盖系统 FFmpeg。对应可选命令为：

```powershell
python -m pip install --target .tools imageio-ffmpeg==0.6.0
node render.cjs --width 3840 --fps 60 --output dataflow-v1-4k60.mp4 --encoder h264_nvenc --ffmpeg .tools/imageio_ffmpeg/binaries/ffmpeg-win-x86_64-v7.1.exe
```

移至其他系统时，调整 `render.cjs` 中的字体路径，以及 `verify-preview.cjs` 中的 Chrome 路径。网页预览不需要 npm 安装。
