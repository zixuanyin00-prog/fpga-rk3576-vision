# 技术树 · 第二版

130 秒，4K / 60 FPS。93 个技术节点、95 条连接；同一张空间技术树与一条连续镜头路径。第一版保留在父目录。

## 观看

- `output/dataflow-v2-tree-4k60.mp4`：4K 母版。
- `output/dataflow-v2-tree-preview-1080p60.mp4`：1080p 预览。
- `preview.html`：时间轴与章节跳转；点击「自由查看」后拖动平移、滚轮缩放，双击恢复运镜。

画面使用短标签、模块名、接口规格、动态连接和局部计算图形。没有标题页切换或长段解释。视野拉远时隐藏细枝标签，靠近时展开；镜头位置和倍率使用连续、无越界回弹的分段插值。

## 技术树范围

- IMX415 → ISP → RGB 增强 / 直方图 → 全分辨率前景 → Sobel / 3×3 形态学 → 公共 RGB。
- 四像素流水；三路独立采集；RAW 默认预览及可选三条 JPEG；32 / 8 / 8 MiB DDR 环。
- 16 MiB coherent pool；6 MiB 预览槽 / 2 MiB ROI 槽；CDC FIFO；Protocol 8 / 96 B 元数据；CRC / commit / STOP。
- 有界调度、两个 RKNN context、NPU core 0 / 1、uint8 NHWC、按完整源帧顺序发布。
- 独立跟踪进程、ID、姿态共识、计数账本、GPIO、图表、异步采证。
- 15 张采集、人工标注、源图划分、原生裁剪、GPU 随机初始化训练、10 张新图测试、ONNX / RKNN、明确选型和模型专用部署。
- `ResistorNet`：MobileNetV2 `features[:14]`、3/6/13 层特征、152 通道拼接、1×1 / DW5×5 / 1×1 / DW3×3 / 1×1 检测头，5 通道检测与 C 类姿态分支。
- AdamW；focal objectness、6×size、1.5×offset、pose cross-entropy。
- 控制面：成像参数、ROI 位置、置信度、确认次数及实读回写。

节点与边的坐标和标签都在 `renderer.js` 中。紫色虚线表示模型回传，琥珀色虚线表示控制返回。树上的模块层级与数据关系是示意排布，不对应硬件物理连线长度。

## 来源与状态

依据项目的三路离线候选、配套运行时源码及 VisionTrainer 源码整理，精确文件和 SHA256 见 `source-manifest.json`。

这是结构动画，不是板端实测。未连接板卡、未加载模型、未修改项目训练原图或服务。512×288 / 72×128 等标签表示已实现的原生裁剪模型接口，不表示本次选择或应用了某个模型。网络内的 ↑2 / ↑4 是特征图重采样，不是训练原图缩放。显示帧率、识别、计数和 GPIO 的现场验收与影片帧率独立。

## 重新渲染

```powershell
npm ci
python audio.py
ffmpeg -y -i soundtrack.wav -c:a aac -b:a 192k soundtrack.m4a
node render.cjs --width 3840 --fps 60 --output dataflow-v2-tree-4k60.mp4
node verify-preview.cjs
python finish.py
```

默认离线编码器是 libx264。当前工作区的制作命令使用父目录保留的兼容 FFmpeg 7.1 与 NVENC：

```powershell
node render.cjs --width 3840 --fps 60 --output dataflow-v2-tree-4k60.mp4 --encoder h264_nvenc --ffmpeg ../.tools/imageio_ffmpeg/binaries/ffmpeg-win-x86_64-v7.1.exe
```

Node 依赖由 package-lock.json 固定；音乐脚本使用 NumPy，采证与打包脚本使用 Pillow。系统字体、Chrome 路径在渲染和验证脚本中。迁移机器时可调整；不分发字体或第三方工具二进制。

音乐由代码合成。MP4 逐帧离线渲染；网页交互刷新率取决于观看设备。
