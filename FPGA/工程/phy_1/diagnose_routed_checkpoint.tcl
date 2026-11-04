import_device ph1_90.db -package PH1A90SEG324 -speed 2
open_project pcie_camera.prj -noanalyze
import_db pcie_camera_routed_before_opt.db
update_timing -mode final
report_timing_status -file checkpoint_diagnostic_status.ts
report_timing_summary -file checkpoint_diagnostic_timing.rpt -ep_num 40
exit
