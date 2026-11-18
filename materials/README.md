# 选题三汇报素材

项目把 FPGA 相机采集与图像处理、PCIe/DMA 数据传输、RK3576 上的视觉识别与显示，以及 PC 端的小样本训练控制台连到自动分拣设备上。

| 汇报内容 | 可用素材 |
| --- | --- |
| 项目与整机 | [成片（约 3 分钟）][film] · [整机和板卡照片](hardware-photos/) |
| 硬件分工和数据流 | [技术文档](../docs/FPGA实时视觉识别与工件自动分拣-技术文档.pdf) · [数据流动画 1080p][animation] · [动画与剪辑工程](video/) |
| 机械结构 | [CAD 目录](cad/) · [建模打包下载][cad] |
| 识别、分拣与界面演示 | [HDMI 近景（26 秒）][hdmi] · [工件样图](replay-samples/) · [原片、剪辑附件与 4K 动画][release] |
| 工程实现 | [FPGA RTL](../FPGA/) · [RK 源码](../RK/) · [训练控制台](../VisionTrainer/) · [11 月 17 日增量源码](../增量源码/2026-11-17-控制台与GPIO/) |

## 成片时间点

| 开始时间 | 画面 |
| --- | --- |
| 0:00 | 项目介绍 |
| 0:06 | FPGA 和 RK 板卡 |
| 0:30.5 | 控制台与 ISP |
| 1:12.2 | 识别与工业检测 |
| 1:40.3 | 姿态检测 |
| 2:19 | 类别检测与连续分拣 |
| 2:54.8 | 系统亮点 |

## 性能数据引用

成片、技术文档和 HDMI 近景记录了各自的演示版本。汇报时请保留指标的统计口径和工况，分别引用 HDMI 新源帧率、ROI 采集率、NPU 推理率与机械分拣吞吐率。视频编码帧率只描述媒体文件。近期 HDMI 调整的实测区间和边界见 [HDMI 调整记录](../RK/HDMI调整页/)。

[release]: https://github.com/zixuanyin00-prog/fpga-rk3576-vision/releases/tag/presentation-2026-11-18
[film]: https://github.com/zixuanyin00-prog/fpga-rk3576-vision/releases/download/presentation-2026-11-18/fpga-vision-competition-v29.mp4
[animation]: https://github.com/zixuanyin00-prog/fpga-rk3576-vision/releases/download/presentation-2026-11-18/dataflow-v2-tree-preview-1080p60.mp4
[cad]: https://github.com/zixuanyin00-prog/fpga-rk3576-vision/releases/download/presentation-2026-11-18/cad-models.zip
[hdmi]: https://github.com/zixuanyin00-prog/fpga-rk3576-vision/releases/download/presentation-2026-11-18/sorting-hdmi-closeup.mp4
