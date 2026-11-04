                                                                       
import_device ph1_90.db -package PH1A90SEG324 -speed 2
open_project pcie_camera.prj -noanalyze
import_db final_pr.db
clear_sdc -user
read_sdc ../constraints/case_ep.sdc
read_sdc ../constraints/jpeg_cdc.sdc
read_sdc ../constraints/status_cdc.sdc
read_sdc ../constraints/ddr_cdc.sdc
update_timing -mode final
check_timing -file refresh_check.rpt
report_timing_status -file refresh_status.ts
report_timing_summary -file refresh_timing.rpt -ep_num 40
report_timing_ignored -file refresh_ignored.rpt -ep_num 60
report_timing_exception -file refresh_exceptions.rpt -ep_num 60
                                                                         
export_db refreshed_pr.db
report_clock_summary -file refresh_clocks.rpt
exit
