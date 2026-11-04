# 第三方依赖与公开范围

完整本地包保留原工程所有文件。公开仓库不分发下表中的供应商材料或专有 SDK 头文件，亦不包含预构建二进制、原始历史发布授权和本机连接设置。没有把供应商文件删除版权头后改为项目自有许可。

- Anlogic TD/IP：从安路科技官方 TD 及相应开发板授权资料取得 PCIe、PLL、RAM、DDR 和 MIPI 相关 IP。
- MiLianKe/PFAC100：从原开发板供应商资料取得相机、初始化、控制与顶层集成参考工程；保留供应商原许可。
- RKNN：从 Rockchip 官方 RKNN SDK 取得匹配版本 rknn_api.h 和运行库；遵守 SDK 许可。
- 第三方公开项目入口：[RKNN Toolkit2](https://github.com/airockchip/rknn-toolkit2)、[RKNN Model Zoo](https://github.com/airockchip/rknn_model_zoo)。这两个链接不能代替特定硬件/IP的授权。

## 需要自行取得的文件

| 原工程路径 | 处理原因 |
| --- | --- |
| `FPGA/工程/al_ip/core_clk_pll.ipc` | 供应商例程、生成或加密 IP；未随公开仓库分发 |
| `FPGA/工程/al_ip/core_clk_pll.v` | 供应商例程、生成或加密 IP；未随公开仓库分发 |
| `FPGA/工程/al_ip/core_clk_pll.vhd` | 供应商例程、生成或加密 IP；未随公开仓库分发 |
| `FPGA/工程/al_ip/ep_cfg_rom.ipc` | 供应商例程、生成或加密 IP；未随公开仓库分发 |
| `FPGA/工程/al_ip/ep_cfg_rom.v` | 供应商例程、生成或加密 IP；未随公开仓库分发 |
| `FPGA/工程/al_ip/ep_cfg_rom.vhd` | 供应商例程、生成或加密 IP；未随公开仓库分发 |
| `FPGA/工程/al_ip/pcie_ep.dat` | 供应商例程、生成或加密 IP；未随公开仓库分发 |
| `FPGA/工程/al_ip/pcie_ep.ipc` | 供应商例程、生成或加密 IP；未随公开仓库分发 |
| `FPGA/工程/al_ip/pcie_ep.txt` | 供应商例程、生成或加密 IP；未随公开仓库分发 |
| `FPGA/工程/al_ip/pcie_ep.v` | 供应商例程、生成或加密 IP；未随公开仓库分发 |
| `FPGA/工程/al_ip/ram_32w_32d.ipc` | 供应商例程、生成或加密 IP；未随公开仓库分发 |
| `FPGA/工程/al_ip/ram_32w_32d.v` | 供应商例程、生成或加密 IP；未随公开仓库分发 |
| `FPGA/工程/al_ip/ram_32w_32d.vhd` | 供应商例程、生成或加密 IP；未随公开仓库分发 |
| `FPGA/工程/phy_1/pcie_camera.tcl` | 供应商例程、生成或加密 IP；未随公开仓库分发 |
| `FPGA/工程/rtl/case_ep/camera_mclk27.v` | 供应商例程、生成或加密 IP；未随公开仓库分发 |
| `FPGA/工程/rtl/case_ep/dbi_access.v` | 供应商例程、生成或加密 IP；未随公开仓库分发 |
| `FPGA/工程/rtl/case_ep/debounce_int.v` | 供应商例程、生成或加密 IP；未随公开仓库分发 |
| `FPGA/工程/rtl/case_ep/dram_8x256.v` | 供应商例程、生成或加密 IP；未随公开仓库分发 |
| `FPGA/工程/rtl/case_ep/elbi_bar.v` | 供应商例程、生成或加密 IP；未随公开仓库分发 |
| `FPGA/工程/rtl/case_ep/ep_dbi_int_mux.v` | 供应商例程、生成或加密 IP；未随公开仓库分发 |
| `FPGA/工程/rtl/case_ep/int_request.v` | 供应商例程、生成或加密 IP；未随公开仓库分发 |
| `FPGA/工程/rtl/case_ep/pcie_camera_case.v` | 供应商例程、生成或加密 IP；未随公开仓库分发 |
| `FPGA/工程/rtl/case_ep/pcie_ep_support.v` | 供应商例程、生成或加密 IP；未随公开仓库分发 |
| `FPGA/工程/rtl/case_ep/pcie_ep_wrapper.v` | 供应商例程、生成或加密 IP；未随公开仓库分发 |
| `FPGA/工程/rtl/case_ep/reset_sync_user.v` | 供应商例程、生成或加密 IP；未随公开仓库分发 |
| `FPGA/工程/rtl/case_ep/sys_ctrl.v` | 供应商例程、生成或加密 IP；未随公开仓库分发 |
| `FPGA/工程/rtl/case_ep/tlp_access_ep.v` | 供应商例程、生成或加密 IP；未随公开仓库分发 |
| `FPGA/工程/rtl/commom/debounce_power_up_rst.v` | 供应商例程、生成或加密 IP；未随公开仓库分发 |
| `FPGA/工程/syn_1/pcie_camera.tcl` | 供应商例程、生成或加密 IP；未随公开仓库分发 |
| `FPGA/工程/vendor/camera/mipi_rx/channel_aligner_wrapper.enc.sv` | 供应商例程、生成或加密 IP；未随公开仓库分发 |
| `FPGA/工程/vendor/camera/mipi_rx/data_lane_rx_wrapper.enc.v` | 供应商例程、生成或加密 IP；未随公开仓库分发 |
| `FPGA/工程/vendor/camera/mipi_rx/hs_rx_wrapper.sv` | 供应商例程、生成或加密 IP；未随公开仓库分发 |
| `FPGA/工程/vendor/camera/mipi_rx/mipi_dphy_rx_over_lvds_wrapper.v` | 供应商例程、生成或加密 IP；未随公开仓库分发 |
| `FPGA/工程/vendor/camera/mipi_rx/rx_clk_gen.v` | 供应商例程、生成或加密 IP；未随公开仓库分发 |
| `FPGA/工程/vendor/camera/ui_ae_set/ui_ae_set.v` | 供应商例程、生成或加密 IP；未随公开仓库分发 |
| `FPGA/工程/vendor/camera/ui_ae_set/ui_key.v` | 供应商例程、生成或加密 IP；未随公开仓库分发 |
| `FPGA/工程/vendor/camera/ui_csi_raw/ui_csi_unpacket_4lane.v` | 供应商例程、生成或加密 IP；未随公开仓库分发 |
| `FPGA/工程/vendor/camera/ui_csi_raw/ui_image_correction.v` | 供应商例程、生成或加密 IP；未随公开仓库分发 |
| `FPGA/工程/vendor/camera/ui_csi_raw/ui_raw10_unpacket_4lane.v` | 供应商例程、生成或加密 IP；未随公开仓库分发 |
| `FPGA/工程/vendor/camera/ui_imx415_cfg/ui_cfg_imx415.v` | 供应商例程、生成或加密 IP；未随公开仓库分发 |
| `FPGA/工程/vendor/camera/ui_imx415_cfg/ui_i2c.v` | 供应商例程、生成或加密 IP；未随公开仓库分发 |
| `FPGA/工程/vendor/camera/ui_imx415_cfg/ui_imx415_reg.v` | 供应商例程、生成或加密 IP；未随公开仓库分发 |
| `FPGA/工程/vendor/camera/ui_pll/ui_pll.ipc` | 供应商例程、生成或加密 IP；未随公开仓库分发 |
| `FPGA/工程/vendor/camera/ui_pll/ui_pll.v` | 供应商例程、生成或加密 IP；未随公开仓库分发 |
| `FPGA/工程/vendor/ddr/ddr_clk.v` | 供应商例程、生成或加密 IP；未随公开仓库分发 |
| `FPGA/工程/vendor/ddr/ui_ddr.adc` | 供应商例程、生成或加密 IP；未随公开仓库分发 |
| `FPGA/工程/vendor/ddr/ui_ddr.ipc` | 供应商例程、生成或加密 IP；未随公开仓库分发 |
| `FPGA/工程/vendor/ddr/ui_ddr.sdc` | 供应商例程、生成或加密 IP；未随公开仓库分发 |
| `FPGA/工程/vendor/ddr/ui_ddr.sv` | 供应商例程、生成或加密 IP；未随公开仓库分发 |
| `FPGA/工程/vendor/ddr/ui_ddr_cfg_rom.txt` | 供应商例程、生成或加密 IP；未随公开仓库分发 |
| `FPGA/工程/vendor/ddr/ui_ddr_mcu_rom.txt` | 供应商例程、生成或加密 IP；未随公开仓库分发 |
| `RK/配套源码/native_engine/rknn_api.h` | 专有 SDK 头文件，原件限制披露 |

本仓库保留可公开的项目维护源码；缺少上述依赖时无法独立完成全工程构建。恢复原工程还须准备匹配的软件环境、路径和内核。未包含模型权重，也未给任何历史模型签发本次授权。
