# 视频与汇报素材

[下载本次发布的视频和素材包](https://github.com/zixuanyin00-prog/fpga-rk3576-vision/releases/tag/presentation-2026-11-18)

| 素材 | 内容 |
| --- | --- |
| [比赛成片 v29](https://github.com/zixuanyin00-prog/fpga-rk3576-vision/releases/download/presentation-2026-11-18/fpga-vision-competition-v29.mp4) | 约 3 分钟，1080p / 60 FPS，含普通话旁白、字幕和章节。 |
| [汇报截图](./汇报截图/) | 9 张 1080p 截图，包含平台全貌、ISP、小样本训练、PCIe 压缩、跨帧跟踪、尺寸测量和异常留证。 |
| [动画 V1 源工程](./dataflow_film_20261028/) | 《一帧图像的数据旅程》，100 秒，包含浏览器播放器、画面绘制和音轨生成源码。 |
| [动画 V2 源工程](./dataflow_film_20261028/v2/) | 《技术树》，130 秒，包含可拖拽、缩放的浏览器播放器。 |
| [比赛剪辑素材](./剪辑/) | 32 场景时间线、字幕、旁白脚本、分镜和渲染代码，以及工件技术演示用图。 |

发布页提供两版动画的 4K60 和 1080p60 MP4。浏览器预览可在本地打开对应的 `preview.html`。动画说明及重渲染命令位于各版源工程。

## 重新剪辑

1. 下载比赛成片作为母版，下载 [v28 时间线](./剪辑/重剪_v28_清理剪辑标注/timeline.json) 与 [SRT 字幕](./剪辑/重剪_v28_清理剪辑标注/全片字幕.srt)。v29 沿用 v28 时间点。
2. 下载实拍素材和分轨音频，按需替换母版片段、字幕或旁白。实拍包中的录屏和相机镜头为去定位、去现场音轨的公开副本。需要保护人物或屏幕内容的镜头做了轻裁或截取。
3. 历史渲染脚本保留了部分此前的工作目录和中间素材依赖。归档中的部分中间成片已经缺失，所以可直接基于母版和本页素材重剪，历史脚本的整片重建仍需要补齐对应的本地中间文件。

| 下载 | 内容 |
| --- | --- |
| [实拍素材 01](https://github.com/zixuanyin00-prog/fpga-rk3576-vision/releases/download/presentation-2026-11-18/competition-source-clips-01.zip) | 7 条，包括振动盘近景、姿态与类别分拣、收集结果。 |
| [实拍素材 02](https://github.com/zixuanyin00-prog/fpga-rk3576-vision/releases/download/presentation-2026-11-18/competition-source-clips-02.zip) | 7 条，包括板卡与转接板、设备总览、控制台与 HDMI 画面。 |
| [实拍素材 03](https://github.com/zixuanyin00-prog/fpga-rk3576-vision/releases/download/presentation-2026-11-18/competition-source-clips-03.zip) | 5 条，包括 ISP 界面实拍、工件样品、控制台流程和部署后的运行画面。 |
| [剪辑音频](https://github.com/zixuanyin00-prog/fpga-rk3576-vision/releases/download/presentation-2026-11-18/competition-edit-audio.zip) | 独立的旁白、程序配乐、证据页音效和 v28 混音 WAV。 |

比赛视频与截图保留了拍摄和制作当时的实验结果。动画说明的是数据流与模块关系。60 FPS 动画规格和当时的画面指标各有对应记录。
