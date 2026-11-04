                                                                                                

                                                                        

                                                                             

derive_clocks

set_max_delay -from [get_regs {u_native_ddr/ROI_QUEUE*queue/ingress/wg[*]}] -to [get_regs {u_native_ddr/ROI_QUEUE*queue/ingress/wg1[*]}] -datapath_only 5.0

set_max_delay -from [get_regs {u_native_ddr/ROI_QUEUE*queue/ingress/rg[*]}] -to [get_regs {u_native_ddr/ROI_QUEUE*queue/ingress/rg1[*]}] -datapath_only 5.0

set_max_delay -from [get_regs {u_native_ddr/ROI_QUEUE*queue/egress/wg[*]}] -to [get_regs {u_native_ddr/ROI_QUEUE*queue/egress/wg1[*]}] -datapath_only 5.0

set_max_delay -from [get_regs {u_native_ddr/ROI_QUEUE*queue/egress/rg[*]}] -to [get_regs {u_native_ddr/ROI_QUEUE*queue/egress/rg1[*]}] -datapath_only 5.0

set_false_path -hold -from [get_regs {u_native_ddr/ROI_QUEUE*queue/ingress/wg[*]}] -to [get_regs {u_native_ddr/ROI_QUEUE*queue/ingress/wg1[*]}]

set_false_path -hold -from [get_regs {u_native_ddr/ROI_QUEUE*queue/ingress/rg[*]}] -to [get_regs {u_native_ddr/ROI_QUEUE*queue/ingress/rg1[*]}]

set_false_path -hold -from [get_regs {u_native_ddr/ROI_QUEUE*queue/egress/wg[*]}] -to [get_regs {u_native_ddr/ROI_QUEUE*queue/egress/wg1[*]}]

set_false_path -hold -from [get_regs {u_native_ddr/ROI_QUEUE*queue/egress/rg[*]}] -to [get_regs {u_native_ddr/ROI_QUEUE*queue/egress/rg1[*]}]



                                                                           

                                                                              

set_false_path -from [get_regs {u_native_ddr/ROI_QUEUE*queue/failed}] -to [get_regs {u_native_ddr/ROI_QUEUE*queue/fault_sync[0]}]

set_false_path -to [get_regs {u_native_ddr/diag_sync0[*]}]



                                                                            

                                                                          

set_false_path -to [get_pins {u_native_ddr/ROI_QUEUE*queue/core_reset*.sr u_native_ddr/ROI_QUEUE*queue/core_reset*.asr u_native_ddr/ROI_QUEUE*queue/mem_reset*.sr u_native_ddr/ROI_QUEUE*queue/mem_reset*.asr u_native_ddr/arb_reset*.sr u_native_ddr/arb_reset*.asr}]



                                                                          

                                                                          

                                                                      

                                                                     

