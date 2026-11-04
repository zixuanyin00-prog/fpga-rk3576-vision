import_device ph1_90.db -package PH1A90SEG324 -speed 2
open_project pcie_camera.prj -noanalyze
import_db final_pr.db
update_timing -mode final
report_timing_exception -file isp_mapping_full.rpt -ep_num 200
exit
