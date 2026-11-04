import_device ph1_90.db -package PH1A90SEG324 -speed 2
open_project pcie_camera.prj -noanalyze
import_db final_pr.db
write_verilog reset_review.v
update_timing -mode final
report_timing_ignored -file reset_ignored_full.rpt -ep_num 500
exit
