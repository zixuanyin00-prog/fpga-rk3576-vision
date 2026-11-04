import_device ph1_90.db -package PH1A90SEG324 -speed 2
open_project pcie_camera.prj -noanalyze
import_db final_pr.db
update_timing -mode final
check_timing -file final_check.rpt
report_timing_status -file final_status.ts
report_timing_summary -file final_timing.rpt -ep_num 40
report_timing_ignored -file final_ignored.rpt -ep_num 60
report_timing_exception -file final_exceptions.rpt -ep_num 60
report_area -file final_area.rpt
                                                                         
                                                                      
report_clock_summary -file final_clocks.rpt
exit
