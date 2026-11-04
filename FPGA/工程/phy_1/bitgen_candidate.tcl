import_device ph1_90.db -package PH1A90SEG324 -speed 2
open_project pcie_camera.prj -noanalyze
import_db final_pr.db
set_param bitgen compress on
bitgen -bit three_stream_switches_20261029.bit
exit
