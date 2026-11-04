# FPGA + RK3576 工业视觉

基于 FPGA 相机采集与 PCIe/DMA 传输、RK3576 视觉运行模块和 PC 小样本训练控制台的项目源码。来源为项目“最终”完整工程快照。公开版统一了代码风格，并删除普通代码注释和 Python 文档字符串；解释、许可证和依赖信息保留在文档中。

| 目录 | 内容 |
| --- | --- |
| FPGA/工程 | 可公开的图像处理 RTL、约束与项目输入 |
| RK/rootfs | Python 板端模块与 systemd 配置参考 |
| RK/配套源码 | Linux 驱动、采集、显示和计算模块 |
| VisionTrainer | PC 控制台、训练、转换、前端及已有测试 |
| 版本与校验 | 文件 SHA256 与只读校验脚本 |

## 准备环境

PC 端使用 Windows、Python、CUDA/PyTorch 和 WSL RKNN 转换环境，依赖见 VisionTrainer 中的 requirements 文件与 setup_gpu.ps1。先按本机环境编辑 `settings.example.json` 与 `console_settings.example.json`，分别复制为 `settings.json`、`console_settings.json`，再使用根目录启动脚本。默认板卡地址已替换为文档示例地址，SSH 私钥和凭据未包含。

RK 端需要匹配内核、ARM64、RKNN/RGA、NumPy/OpenCV 和 X11 环境。`rootfs` 只表示文件目标布局，不可直接覆盖系统根目录；systemd 文件仅作部署参考。未包含历史模型授权、固化发布清单和预构建二进制。电磁阀示例配置默认关闭，必须按现场重新配置。

SSH 连接只使用用户自己的 known_hosts；首次连接前应独立确认设备指纹。原板卡固定公钥已移除。最高优先级的 fixed-flash-release 配置仍引用需在本机重新生成的 release.json，不能直接启用这套历史启动配置；重新准备发布清单时仍须取得本次模型选择。

FPGA 工程基于 Anlogic TD 6.2、PH1A90SEG324、速度等级 -2，顶层 pcie_camera_case。厂商 IP、加密 HDL、供应商例程和 RKNN 专有头文件须从相应官方 SDK/开发板资料取得，见 THIRD_PARTY.md。它们未随公开仓库分发，当前仓库不能独立完成 FPGA 全构建；项目中的原固定构建路径需按本机位置调整。

## 模型与图像

模型用于现场小样本场景。每次测试、加载、部署、切换或回退前，应明确选择实际训练北京时间、场景和模型哈希，不能默认采用最新或历史模型。仓库不含权重或训练集。原生 RGB 训练图仅裁剪，不缩放、不拉伸、不降采样、不作有损压缩。其他边界见 AGENTS.md。

## 校验与验证范围

运行 `python 版本与校验/verify_release.py` 可检查交付文件 SHA256。排版采用 Python AST、前端 AST 及 C/RTL 词法核对；去注释保留业务字符串和必需指令。完整本地包另有详细校验记录。

本次未运行模型、控制台或板卡，未取得当前组合的新源帧去重显示 60 FPS、识别准确率或物理动作验收。已有测试中的部分用例依赖原数据、SDK 或硬件，不能把源码语法检查当成运行验收。

## 许可

项目自有源码采用 MIT；`RK/配套源码/driver` 按 GPL-2.0-or-later 分发，保留 MODULE_LICENSE 元数据。具体范围见 LICENSE、LICENSES 和 THIRD_PARTY.md。排除的供应商依赖不受本仓库 MIT 授权。
