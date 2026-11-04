                                                                      
                                                             

                                                                         
                                                                           
                                                                      
                                                                           





                                                                  
                                                                             

                                                                                 
set_max_delay -from [get_regs {u_camera_frontend/raw_cdc/raw_fifo/wg[*]}] -to [get_regs {u_camera_frontend/raw_cdc/raw_fifo/wg1[*]}] -datapath_only 5.0
set_max_delay -from [get_regs {u_camera_frontend/raw_cdc/raw_fifo/rg[*]}] -to [get_regs {u_camera_frontend/raw_cdc/raw_fifo/rg1[*]}] -datapath_only 5.0
set_false_path -to [get_regs {u_camera_frontend/raw_cdc/error_sync[0]}]

                                                                         
                                                                     
set_false_path -hold -from [get_regs {u_camera_frontend/raw_cdc/raw_fifo/wg[*]}] -to [get_regs {u_camera_frontend/raw_cdc/raw_fifo/wg1[*]}]
set_false_path -hold -from [get_regs {u_camera_frontend/raw_cdc/raw_fifo/rg[*]}] -to [get_regs {u_camera_frontend/raw_cdc/raw_fifo/rg1[*]}]
