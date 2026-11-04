                                                                       
                                                                             
                                                                            
                                                                            
set_false_path -from [get_clocks {u_camera_frontend/X_mipi_dphy_rx_over_lvds_wrapper/u_rx_clk_gen/U_io_clk_div_1.clkdivout}] -to [get_regs {dma_error_sync[0]}]

                                                                            
                                                                         
                                                                          
                                                                          
                                                                  
set_false_path -from [get_clocks {core_clk core_clk_c}] -to [get_pins {u_camera_frontend/raw_cdc/cam_reset*.sr u_camera_frontend/raw_cdc/cam_reset*.asr}]
