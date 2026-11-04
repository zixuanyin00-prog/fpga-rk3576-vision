
                                   
                                                                          
                                                                  
                                                                      
                                                                     
                                                          
                                                                             
                                                                     
create_clock -name aux_clk  -period 40.000   -waveform {0 20}     [get_ports app_auxclk_i]
create_clock -name mipi_clk -period 1.333333 -waveform {0 0.667}  [get_ports I_clk_hs_p]
create_clock -name core_clk -period 8   -waveform {0 4}   [get_pins u_pcie_ep_support.u_pcie_ep_wrapper.u_pcie_ep.uep_PH1_PHY_PCIE.core_clk]
derive_clocks
rename_clock -name {core_clk_c} -source [get_pins {u_pcie_ep_support/u_sys_ctrl/u_core_clk_pll/pll_inst.refclk}] -master_clock {pll_inst/pll_inst.refclk} [get_pins {u_pcie_ep_support/u_sys_ctrl/u_core_clk_pll/pll_inst.clkc[1]}]

                                                                    

                                                                      
                                                                        
                                                                      
                                                                       
                                  
                            
set_clock_groups -asynchronous \
    -group [get_clocks {aux_clk \
        u_camera_frontend/X_ui_pll/pll_inst.clkc[0] \
        u_camera_frontend/X_ui_pll/pll_inst.clkc[1]}] \
    -group [get_clocks {mipi_clk \
        u_camera_frontend/X_mipi_dphy_rx_over_lvds_wrapper/u_rx_clk_gen/U_io_clk_div_0.clkout \
        u_camera_frontend/X_mipi_dphy_rx_over_lvds_wrapper/u_rx_clk_gen/U_io_clk_div_0.clkdivout \
        u_camera_frontend/X_mipi_dphy_rx_over_lvds_wrapper/u_rx_clk_gen/U_io_clk_div_1.clkdivout \
        core_clk_c core_clk}]

                                                                       
create_generated_clock -name camera_i2c_internal -source [get_pins {u_camera_frontend/X_ui_pll/pll_inst.clkc[1]}] -divide_by 250 [get_regs {u_camera_frontend/X_ui_cfg_imx415/X_ui_i2c/scl_clk}]
