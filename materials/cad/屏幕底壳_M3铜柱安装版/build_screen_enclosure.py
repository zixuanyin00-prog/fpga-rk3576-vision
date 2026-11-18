"""Blender 5.x: generate the screen back shell, removable stand, STL and previews.

Run: blender --background --factory-startup --python build_screen_enclosure.py
All numerical mesh coordinates and STL coordinates are millimetres.
Mount datum: inner floor to corner mounts 10 mm, corner mounts to screen front
10 mm, as clarified by the user. Four perimeter walls and a removable bezel.
"""
from pathlib import Path
import bpy
import bmesh
import json
import math
import struct
import hashlib
from mathutils import Matrix, Vector

OUT = Path(__file__).resolve().parent
STL = OUT / "STL_毫米"
STL.mkdir(exist_ok=True)

# Design record. Edit this generator to resize the model; the emitted JSON is
# an output record, not a configuration input. Shell envelope, cavity, depth,
# mounting pitch and slot dimensions below feed directly into construction.
P = {
    "screen_envelope_xy": [233.0, 160.0],
    "screen_mount_pitch_xy_UNCONFIRMED": [227.0, 151.0],
    "mount_hole_in_photo": 3.0,
    "screen_fastener_CONFIRMED": "M3, existing 10 mm brass standoff BELOW each mounting ear",
    "screen_mount_slot_diameter": 3.6,
    "screen_mount_slot_travel_x": 2.0,
    "screen_mount_pitch_x_supported_range": [223.0,227.0],
    "corner_rear_fastener_recess_depth": 1.2,
    "corner_rear_fastener_recess_width": 9.0,
    "cavity_xy": [236.2, 162.2],
    "wall": 3.0,
    "floor": 3.0,
    "inner_floor_to_corner_mount": 10.0,
    "corner_mount_to_screen_front": 10.0,
    "front_bezel_thickness": 2.0,
    "existing_brass_standoff_height_CONFIRMED": 10.0,
    "brass_outer_diameter_CHECK_ENVELOPE": 8.0,
    "existing_screw_head_CHECK_ENVELOPE_diameter_height": [8.0,6.0],
    "independent_bezel_mounts_xy": [[-80,-86],[80,-86],[-80,86],[80,86]],
    "bezel_underface_screen_clearance": 0.8,
    "bezel_underface_relief_xy": [236.4,142.6],
    "rear_stand_boss_projection": 3.0,
    "rear_vent_slot_width": 4.0,
    "rear_vent_slot_length": 21.0,
    "stand_tilt_from_vertical_degrees": 15.0,
    "stand_attachment_pitch_xy": [82.0, 40.0],
    "stand_base_footprint_xy": [184.0, 145.0],
    "shell_back_center_above_desk": 111.0,
    "perimeter": "Continuous walls and an independently screwed removable bezel; existing screen fasteners remain installed",
    "port_positions": "UNKNOWN: no large connector bays; add only measured local ports later",
    "fit_status": "DIMENSIONAL DRAFT: not physically printed, fitted, load- or thermally-tested",
}
(OUT / "尺寸记录.json").write_text(json.dumps(P, ensure_ascii=False, indent=2), encoding="utf-8")

bpy.ops.wm.read_factory_settings(use_empty=True)
scene = bpy.context.scene
scene.name = "01_整机装配_屏幕仅示意"
scene.unit_settings.system = "METRIC"
scene.unit_settings.scale_length = .001
scene.unit_settings.length_unit = "MILLIMETERS"
temp = bpy.data.collections.new("_CSG_work")
scene.collection.children.link(temp)
parts_collection = bpy.data.collections.new("打印零件_毫米")
scene.collection.children.link(parts_collection)
ref_collection = bpy.data.collections.new("屏幕与紧固件_仅示意_不打印")
scene.collection.children.link(ref_collection)

def mesh_object(name, vertices, faces, collection=temp):
    mesh = bpy.data.meshes.new(name)
    mesh.from_pydata(vertices, [], faces)
    mesh.update()
    obj = bpy.data.objects.new(name, mesh)
    collection.objects.link(obj)
    bm = bmesh.new()
    bm.from_mesh(mesh)
    bmesh.ops.recalc_face_normals(bm, faces=list(bm.faces))
    bm.to_mesh(mesh)
    bm.free()
    return obj

def prism(name, polygon, z0, z1):
    n = len(polygon)
    return mesh_object(name, [(x,y,z) for z in (z0,z1) for x,y in polygon],
        [tuple(reversed(range(n))), tuple(range(n,2*n))] +
        [(i,(i+1)%n,(i+1)%n+n,i+n) for i in range(n)])

def rounded(name, w, h, r, z0, z1, cx=0, cy=0, segments=10):
    points=[]
    for x,y,start in [(w/2-r,h/2-r,0),(-w/2+r,h/2-r,90),
                      (-w/2+r,-h/2+r,180),(w/2-r,-h/2+r,270)]:
        for j in range(segments+1):
            a=math.radians(start+90*j/segments)
            points.append((cx+x+r*math.cos(a),cy+y+r*math.sin(a)))
    return prism(name, points, z0, z1)

def box(name, x0,x1,y0,y1,z0,z1):
    return prism(name, [(x0,y0),(x1,y0),(x1,y1),(x0,y1)], z0,z1)

def cylinder(name, radius, z0,z1,cx=0,cy=0, sides=48):
    return prism(name, [(cx+radius*math.cos(2*math.pi*i/sides),
                        cy+radius*math.sin(2*math.pi*i/sides)) for i in range(sides)],z0,z1)

def boolean(obj, cutters, operation="DIFFERENCE"):
    # One batched Boolean per independent feature group; overlapping operands
    # belong to the same collection and are resolved by Blender's exact solver.
    col=bpy.data.collections.new("_boolean")
    scene.collection.children.link(col)
    for cutter in cutters:
        for old in list(cutter.users_collection): old.objects.unlink(cutter)
        col.objects.link(cutter)
    mod=obj.modifiers.new(operation,"BOOLEAN")
    mod.operation=operation
    mod.operand_type="COLLECTION"
    mod.collection=col
    mod.solver="EXACT"
    bpy.context.view_layer.objects.active=obj
    obj.select_set(True)
    bpy.ops.object.modifier_apply(modifier=mod.name)
    obj.select_set(False)
    for cutter in cutters: bpy.data.objects.remove(cutter,do_unlink=True)
    bpy.data.collections.remove(col)
    return obj

def to_parts(obj, name):
    obj.name=name
    for col in list(obj.users_collection): col.objects.unlink(obj)
    parts_collection.objects.link(obj)
    return obj

def paint(obj, mat):
    obj.data.materials.clear()
    obj.data.materials.append(mat)
    for poly in obj.data.polygons: poly.material_index=0

def material(name, color, metallic=0, roughness=.5):
    mat=bpy.data.materials.new(name)
    mat.diffuse_color=(*color,1)
    mat.use_nodes=True
    node=mat.node_tree.nodes.get("Principled BSDF")
    node.inputs["Base Color"].default_value=(*color,1)
    node.inputs["Metallic"].default_value=metallic
    node.inputs["Roughness"].default_value=roughness
    return mat

blue=material("底壳_深海蓝",(.065,.22,.33),.12,.4)
orange=material("支架_暖橙",(.83,.34,.09),.05,.45)
dark=material("底座_石墨灰",(.10,.13,.16),.15,.42)
metal=material("紧固件_金属_不打印",(.55,.61,.65),.8,.24)
glass=material("屏幕玻璃_尺寸示意",(.012,.025,.04),.28,.17)
black=material("屏幕边框_仅示意",(.028,.035,.04),.12,.4)
white=material("屏幕显示示意",(.15,.55,.72),.1,.45)

W,H=P["cavity_xy"]
wall=P["wall"]
MOUNT=P["floor"]+P["inner_floor_to_corner_mount"]
FRONT=MOUNT+P["corner_mount_to_screen_front"]
DEPTH=FRONT+P["front_bezel_thickness"]
BACK=P["rear_stand_boss_projection"]
P["corner_mount_z"]=MOUNT
P["screen_front_z"]=FRONT
P["main_body_front_z"]=DEPTH
(OUT / "尺寸记录.json").write_text(json.dumps(P, ensure_ascii=False, indent=2), encoding="utf-8")
shell=rounded("shell_raw",W+2*wall,H+2*wall,8,0,FRONT)
boolean(shell,[rounded("cavity",W,H,3,P["floor"],DEPTH+2)])

add=[rounded("stand_load_island_external",98,64,6,-BACK,P["floor"])]
pitchx,pitchy=P["screen_mount_pitch_xy_UNCONFIRMED"]
# Existing brass columns supply the full 10 mm mounting height: no printed
# pillars are added at the screen's four mounting axes.
for x,y in P["independent_bezel_mounts_xy"]:
    add.append(cylinder("independent_bezel_boss",5,0,FRONT,cx=x,cy=y))
# Reinforcement stays outside the 10 mm space below the screen mounting datum.
for y in (-26,26): add.append(box("external_load_rib",-W/2-1,W/2+1,y-2.5,y+2.5,-2,1))
boolean(shell,add,"UNION")

cuts=[]
# No broad cable bays: all four walls extend continuously to the front bezel.
# 72 rear through-slots; preserve the attachment island, joining lands and ribs.
vent_count=0
for y in (-65,-39,39,65):
    for x in (-102,-94,-86,-78,-70,-62,62,70,78,86,94,102):
        vwidth=P["rear_vent_slot_width"]
        cuts.append(rounded("rear_air_slot",vwidth,P["rear_vent_slot_length"],vwidth/2,-1,8,cx=x,cy=y,segments=8))
        vent_count+=1
for y in (-13,13):
    for x in (-102,-94,-86,-78,-70,-62,62,70,78,86,94,102):
        vwidth=P["rear_vent_slot_width"]
        cuts.append(rounded("rear_air_slot",vwidth,P["rear_vent_slot_length"]-2,vwidth/2,-1,8,cx=x,cy=y,segments=8))
        vent_count+=1
# Side low-level slots retain ventilation through the closed perimeter.
for sign in (-1,1):
    for y in (-53,-21,21,53):
        o=rounded("side_air_slot",4,18,2,-4,4,cy=y)
        # Local X is height; Z is wall normal, Y remains vertical in plan.
        o.data.transform(Matrix.Rotation(math.pi/2,4,"Y"))
        o.location=(sign*(W/2+wall/2),0,6.5)
        cuts.append(o)
for x in (-pitchx/2,pitchx/2):
    for y in (-pitchy/2,pitchy/2):
        dia=P["screen_mount_slot_diameter"]
        slot_x=x-math.copysign(P["screen_mount_slot_travel_x"]/2,x)
        cuts.append(rounded("M3_screen_floor_slot",dia+P["screen_mount_slot_travel_x"],dia,dia/2,-1,P["floor"]+1,cx=slot_x,cy=y))
for x,y in P["independent_bezel_mounts_xy"]:
    cuts.append(cylinder("bezel_M3_bore",1.8,FRONT-6,FRONT+1,cx=x,cy=y))
# Stand bolts M4: 82 x 40 mm pattern. The centre land is 6 mm thick.
stand_x,stand_y=[v/2 for v in P["stand_attachment_pitch_xy"]]
for x in (-stand_x,stand_x):
    for y in (-stand_y,stand_y): cuts.append(cylinder("M4_stand",2.25,-BACK-1,5,cx=x,cy=y))
# Two external bridge strips join the split shell, with flush inner screw heads.
for x in (-18,18):
    for y in (-55,55): cuts.append(cylinder("M3_join",1.7,-1,8,cx=x,cy=y))
boolean(shell,cuts)
# Underside sockets are cut in a second batch because they overlap top holes.
cuts=[]
for x in (-pitchx/2,pitchx/2):
    for y in (-pitchy/2,pitchy/2):
        slot_x=x-math.copysign(P["screen_mount_slot_travel_x"]/2,x)
        dia=P["corner_rear_fastener_recess_width"]
        half=(dia+P["screen_mount_slot_travel_x"])/2
        cuts.append(box("rear_M3_head_nut_washer_recess",slot_x-half,slot_x+half,y-dia/2,y+dia/2,
                        -1,P["corner_rear_fastener_recess_depth"]))
for x,y in P["independent_bezel_mounts_xy"]:
    cuts.append(cylinder("bezel_nut_tool_access",3.8,-1,18.5,cx=x,cy=y))
for x in (-stand_x,stand_x):
    for y in (-stand_y,stand_y):
        # AF 7.27 mm hex pocket, 3.4 mm deep; retain the M4 nut with a tiny
        # adhesive dab on its exterior before fitting the screen, not on threads.
        cuts.append(cylinder("M4_captive_nut_hex",4.2,P["floor"]-3.4,P["floor"]+.5,cx=x,cy=y,sides=6))
for x in (-18,18):
    for y in (-55,55):
        # A 90-degree M3 countersink keeps the inner floor completely flush.
        n=48
        radii=[(1.7,1.5),(3.5,3.3)]
        vertices=[(x+r*math.cos(2*math.pi*i/n),y+r*math.sin(2*math.pi*i/n),z)
                  for r,z in radii for i in range(n)]
        faces=[tuple(reversed(range(n))),tuple(range(n,2*n))]+[(i,(i+1)%n,(i+1)%n+n,i+n) for i in range(n)]
        cuts.append(mesh_object("flush_M3_countersink",vertices,faces))
boolean(shell,cuts)
# Separate hex cuts overlap the tool-access bores intentionally at z18.5.
boolean(shell,[cylinder("bezel_M3_nut_socket",5.8/math.sqrt(3),18.4,21.1,cx=x,cy=y,sides=6)
               for x,y in P["independent_bezel_mounts_xy"]])
to_parts(shell,"01_底壳_整件_四周连续包边")
paint(shell,blue)
shell["尺寸状态"]="内底面到卡口基准10mm，卡口基准到屏幕前面10mm；平面孔距和接口待实测"
shell["主体厚度说明"]="普通背板外面z0至包边正面z25；背面加强座突出3mm，外置连接件另计"
shell["背板散热孔数量"]=vent_count
shell["屏幕固定"]="现有10mm铜柱直接放在内底面z3；底板开M3螺杆5.6x3.6槽；四角无打印高柱"
shell["前框固定"]="四个独立外侧M3固定点，不使用或挤压屏幕原有螺钉与安装耳"

# Split copies are exported, then kept in the printing scene only.
halves=[]
for name,x0,x1 in [("02_底壳_左半",-300,0),("03_底壳_右半",0,300)]:
    obj=bpy.data.objects.new(name,shell.data.copy())
    temp.objects.link(obj)
    boolean(obj,[box("split_halfspace",x0,x1,-300,300,-5,100)],"INTERSECT")
    to_parts(obj,name)
    halves.append(obj)

bridge=rounded("shell_join_bridge",68,16,3,0,4)
boolean(bridge,[cylinder("M3",1.7,-1,5,cx=x) for x in (-18,18)])
to_parts(bridge,"04_底壳背面连接片_打印2件")
paint(bridge,orange)

# A removable bezel covers the unused border above and below the active screen.
# The screen fits through the open shell first; the bezel is then bolted down.
bezel=rounded("front_bezel",W+2*wall,H+2*wall,8,FRONT,DEPTH)
boolean(bezel,[rounded("visible_window",225,132,2,FRONT-1,DEPTH+1)])
boolean(bezel,[cylinder("bezel_tab",5,FRONT,DEPTH,cx=x,cy=y)
               for x,y in P["independent_bezel_mounts_xy"]],"UNION")
boolean(bezel,[cylinder("bezel_independent_M3",1.8,FRONT-1,DEPTH+1,cx=x,cy=y)
               for x,y in P["independent_bezel_mounts_xy"]] +
              [rounded("screen_face_relief",*P["bezel_underface_relief_xy"],.2,FRONT-1,FRONT+P["bezel_underface_screen_clearance"])])
to_parts(bezel,"08_可拆前包边框_整件")
paint(bezel,blue)
bezel["不接触屏幕安装耳"]="无下压柱；原四角螺丝保持原装，前框另用四颗M3固定"
bezel["屏幕前面避让mm"]=P["bezel_underface_screen_clearance"]
bezel_halves=[]
for name,x0,x1 in [("09_前包边框_左半",-300,0),("10_前包边框_右半",0,300)]:
    obj=bpy.data.objects.new(name,bezel.data.copy()); temp.objects.link(obj)
    boolean(obj,[box("split_bezel_halfspace",x0,x1,-300,300,-5,100)],"INTERSECT")
    to_parts(obj,name); bezel_halves.append(obj)

# Actual assembly checks in local millimetre coordinates, before rotating the
# assembly. These envelopes are explicit bounds, not a claimed exact screen CAD.
fit_report={"confirmed":{"screws":"M3","lower_brass_standoff_height_mm":10,
                         "floor_to_mount_mm":10,"mount_to_screen_front_mm":10},
            "check_envelopes":{"screen_xy_mm":[233,160],"brass_diameter_mm":8,
                               "head_diameter_height_above_mount_mm":[8,6]},
            "physical_fit_verified":False,"checks":[]}

def check_overlap(label, fixed, moving, offsets):
    volumes=[]
    for dz in offsets:
        target=bpy.data.objects.new("_fit_intersection",fixed.data.copy()); temp.objects.link(target)
        probe=bpy.data.objects.new("_fit_probe",moving.data.copy()); temp.objects.link(probe)
        probe.location.z=dz
        boolean(target,[probe],"INTERSECT")
        bm=bmesh.new(); bm.from_mesh(target.data)
        volume=abs(bm.calc_volume(signed=True)) if bm.faces else 0
        bm.free(); bpy.data.objects.remove(target,do_unlink=True)
        volumes.append({"z_offset_mm":dz,"overlap_mm3":volume})
        assert volume<.005,(label,dz,volume)
    fit_report["checks"].append({"check":label,"samples":volumes,"passed":True})

envelope=box("screen_outer_envelope_for_insertion",-116.5,116.5,-80,80,P["floor"],FRONT)
check_overlap("233x160 envelope lowers straight into shell, bezel removed",shell,envelope,[30,15,10,5,1,.2,0])
bpy.data.objects.remove(envelope,do_unlink=True)

for pitch in (223,227):
    for x in (-pitch/2,pitch/2):
        for y in (-pitchy/2,pitchy/2):
            guard=cylinder("8mm_brass_guard",4,P["floor"],MOUNT,cx=x,cy=y)
            check_overlap(f"8mm brass fits, pitch {pitch}, axis {x},{y}",shell,guard,[15,5,0])
            bpy.data.objects.remove(guard,do_unlink=True)
            shaft=cylinder("M3_shaft_guard",1.5,-1,P["floor"]+1,cx=x,cy=y)
            check_overlap(f"M3 shaft passes floor slot at {x},{y}",shell,shaft,[0])
            bpy.data.objects.remove(shaft,do_unlink=True)
            bottom=cylinder("rear_fastener_8mm_guard",4,-1,P["corner_rear_fastener_recess_depth"],cx=x,cy=y)
            check_overlap(f"8mm rear head/washer clearance at {x},{y}",shell,bottom,[0])
            bpy.data.objects.remove(bottom,do_unlink=True)

front_guard=box("screen_front_guard",-116.5,116.5,-70,70,MOUNT,FRONT)
head_guards=[cylinder("head_guard",4,MOUNT,MOUNT+6,cx=x,cy=y)
             for x in (-pitchx/2,pitchx/2) for y in (-pitchy/2,pitchy/2)]
boolean(front_guard,head_guards,"UNION")
# Move screen down to represent the bezel being lowered from above.
check_overlap("bezel lowers over original M3 heads without clamping ears",bezel,front_guard,[-20,-10,-5,-1,0])
bpy.data.objects.remove(front_guard,do_unlink=True)
fit_report["nominal_clearances_mm"]={"side_x":1.6,"side_y":1.1,"bezel_to_screen_front":.8,
                                     "independent_boss_to_160mm_envelope":1.0,
                                     "M3_diametral_clearance_before_printing":.6}
(OUT/"装配路径与铜柱避让检查.json").write_text(json.dumps(fit_report,ensure_ascii=False,indent=2),encoding="utf-8")
print("ASSEMBLY_ENVELOPE_CHECKS_PASSED",flush=True)

tilt=math.radians(P["stand_tilt_from_vertical_degrees"])
T=Matrix.Translation((0,0,P["shell_back_center_above_desk"])) @ Matrix.Rotation(math.pi/2-tilt,4,"X")
STAND_T=T @ Matrix.Translation((0,0,-BACK))
for obj in [shell,*halves,bezel,*bezel_halves]: obj.matrix_world=T

# Stand frames are built in assembled world coordinates. Their front boundaries
# follow the rear of the screen until below the bottom edge, then extend forward.
upper=STAND_T @ Vector((0,37,-5.2))
knee=STAND_T @ Vector((0,-26.5,-5.2))
low_front=(20-P["shell_back_center_above_desk"])*math.tan(tilt)+(5.2+BACK)/math.cos(tilt)
poly=[(-50,6),(85,6),(85,15),(upper.y,upper.z),(knee.y,knee.z),(low_front,20),(-50,15)]
frame=prism("side_frame",poly,26,36)
# Map prism local (u,v,w) to assembly (X,Y,Z)=(w,u,v).
frame.data.transform(Matrix(((0,0,1,0),(1,0,0,0),(0,1,0,0),(0,0,0,1))))
opening=prism("frame_lightening",[(0,30),(65,30),(23,110)],24,38)
opening.data.transform(Matrix(((0,0,1,0),(1,0,0,0),(0,1,0,0),(0,0,0,1))))
boolean(frame,[opening])
flange=box("screen_attachment_flange",26,48,-35,35,-6,0)
flange.data.transform(STAND_T)
foot=rounded("stand_foot",22,135,3,6,14,cx=37,cy=17.5)
boolean(frame,[flange,foot],"UNION")
cuts=[]
for y in (-stand_y,stand_y):
    o=cylinder("stand_M4_normal_to_screen",2.25,-8,2,cx=stand_x,cy=y)
    o.data.transform(STAND_T)
    cuts.append(o)
for y in (-40,75): cuts.append(cylinder("foot_M4",2.25,4,16,cx=41,cy=y))
boolean(frame,cuts)
to_parts(frame,"05_桌面支架_右侧")
paint(frame,orange)
left=bpy.data.objects.new("06_桌面支架_左侧",frame.data.copy())
parts_collection.objects.link(left)
left.data.transform(Matrix.Diagonal((-1,1,1,1)))
bm=bmesh.new(); bm.from_mesh(left.data)
bmesh.ops.recalc_face_normals(bm,faces=list(bm.faces)); bm.to_mesh(left.data); bm.free()

base=rounded("front_base_crossbar",184,28,6,0,6,cy=-41)
boolean(base,[rounded("rear_base_crossbar",184,28,6,0,6,cy=76),
              rounded("right_base_rail",26,145,4,0,6,cx=37,cy=17.5),
              rounded("left_base_rail",26,145,4,0,6,cx=-37,cy=17.5)],"UNION")
boolean(base,[cylinder("base_M4",2.25,-1,8,cx=x,cy=y) for x in (-41,41) for y in (-40,75)])
boolean(base,[cylinder("button_head_recess",4.5,-1,2.4,cx=x,cy=y) for x in (-41,41) for y in (-40,75)] +
             [cylinder("rubber_pad_recess",6.2,-1,1,cx=x,cy=y) for x in (-78,78) for y in (-41,76)])
to_parts(base,"07_桌面加宽底座_184x145mm")
paint(base,dark)

# A small first print verifies the actual printer's hole shrinkage and hardware.
coupon=rounded("hardware_fit_coupon",100,44,3,0,3)
coupon_holes=[]
for x,d in zip((-36,-12,12,36),(3.2,3.4,3.6,3.8)):
    coupon_holes.append(cylinder("M3_coupon_hole",d/2,-1,4,cx=x,cy=10))
for x,d in zip((-36,-12,12,36),(6.5,7,7.5,8)):
    coupon_holes.append(cylinder("brass_size_check",d/2,-1,4,cx=x,cy=-10))
for x,af in [(-24,5.8),(24,7.27)]:
    coupon_holes.append(cylinder("nut_AF_check",af/math.sqrt(3),-1,4,cx=x,sides=6))
boolean(coupon,coupon_holes)
letters=[]
for y,diameters in [(16.3,(3.2,3.4,3.6,3.8)),(-20,(6.5,7,7.5,8))]:
    for x,d in zip((-36,-12,12,36),diameters):
        font=bpy.data.curves.new("coupon_label","FONT"); font.body=f"{d:.1f}"
        font.align_x="CENTER"; font.size=3.2; font.extrude=.2
        obj=bpy.data.objects.new("coupon_mm_label",font); temp.objects.link(obj)
        obj.location=(x,y,3)
        bpy.ops.object.select_all(action="DESELECT"); obj.select_set(True)
        bpy.context.view_layer.objects.active=obj
        bpy.ops.object.convert(target="MESH")
        letters.append(bpy.context.object)
boolean(coupon,letters,"UNION")
to_parts(coupon,"11_先打印_M3与铜柱孔径试片")
paint(coupon,orange)

# STL export: resolve transforms, orient on bed, validate closed connected solids,
# write binary millimetre coordinates and read back the actual exported vertices.
checks={"parameters":P,"rear_vent_slots":vent_count,"parts":[],
        "limitations":["No physical print, fit, stability or thermal verification",
                        "Brass outer diameter and screw heads checked against 8mm bounds, actual hardware not measured; rear profile and connectors unmeasured",
                        "Mount pitch 227x151 inferred from front photo; must be measured",
                        "No exhaustive self-intersection test or printer-specific slicing"]}

def export_part(obj, filename, transform, qty):
    bm=bmesh.new(); bm.from_mesh(obj.data)
    bm.transform(transform @ obj.matrix_world)
    bmesh.ops.remove_doubles(bm,verts=list(bm.verts),dist=.00001)
    bmesh.ops.recalc_face_normals(bm,faces=list(bm.faces))
    low=Vector([min(v.co[k] for v in bm.verts) for k in range(3)])
    high=Vector([max(v.co[k] for v in bm.verts) for k in range(3)])
    shift=Vector((-(low.x+high.x)/2,-(low.y+high.y)/2,-low.z))
    for v in bm.verts: v.co+=shift
    bm.normal_update()
    bad=[e for e in bm.edges if not e.is_manifold or not e.is_contiguous]
    assert not bad, (obj.name,"non-manifold",len(bad))
    remaining=set(bm.verts); components=0
    while remaining:
        components+=1; stack=[remaining.pop()]
        while stack:
            vertex=stack.pop()
            for edge in vertex.link_edges:
                other=edge.other_vert(vertex)
                if other in remaining: remaining.remove(other); stack.append(other)
    assert components==1,(obj.name,"disconnected solids",components)
    volume=bm.calc_volume(signed=True)
    assert volume>0,(obj.name,"inverted/zero volume")
    flat_mesh=bpy.data.meshes.new(filename)
    bm.to_mesh(flat_mesh)
    for mat in obj.data.materials: flat_mesh.materials.append(mat)
    bmesh.ops.triangulate(bm,faces=list(bm.faces))
    bm.normal_update()
    path=STL/filename
    with path.open("wb") as f:
        f.write(b"Millimetres; 100% scale; dimensional draft; physical fit unverified".ljust(80,b"\0"))
        f.write(struct.pack("<I",len(bm.faces)))
        for face in bm.faces:
            coords=[*face.normal]
            for vertex in face.verts: coords.extend(vertex.co)
            f.write(struct.pack("<12fH",*coords,0))
    data=path.read_bytes(); n=struct.unpack_from("<I",data,80)[0]
    assert len(data)==84+n*50
    vertices=[]; edges={}; stl_volume=0
    for row in struct.iter_unpack("<12fH",data[84:]):
        tri=[tuple(row[k:k+3]) for k in (3,6,9)]
        vertices.extend(tri)
        a,b,c=[Vector(v) for v in tri]
        stl_volume+=a.dot(b.cross(c))/6
        for i in range(3):
            key=tuple(sorted((tri[i],tri[(i+1)%3])))
            edges[key]=edges.get(key,0)+1
    assert all(n==2 for n in edges.values()),filename
    assert stl_volume>0,filename
    mn=[min(v[k] for v in vertices) for k in range(3)]
    mx=[max(v[k] for v in vertices) for k in range(3)]
    dims=[mx[k]-mn[k] for k in range(3)]
    assert abs(mn[2])<.0001,filename
    checks["parts"].append({"object":obj.name,"file":filename,"quantity":qty,
        "print_dimensions_mm":dims,"triangles":n,"connected_components":components,
        "closed_manifold":True,"stl_edge_incidence_2":True,"volume_mm3":volume,
        "estimated_solid_PETG_mass_g":volume*.00127,"sha256":hashlib.sha256(data).hexdigest()})
    bm.free()
    print("STL_CHECKED",filename,[round(v,2) for v in dims],flush=True)
    return flat_mesh

flat=[]
for obj,file,transform,qty in [
    (shell,"01_shell_whole_mm.stl",T.inverted(),1),
    (halves[0],"02_shell_left_mm.stl",T.inverted(),1),
    (halves[1],"03_shell_right_mm.stl",T.inverted(),1),
    (bridge,"04_shell_join_bridge_PRINT_2_mm.stl",Matrix.Identity(4),2),
    (frame,"05_stand_right_SIDE_DOWN_mm.stl",Matrix.Rotation(-math.pi/2,4,"Y"),1),
    (left,"06_stand_left_SIDE_DOWN_mm.stl",Matrix.Rotation(math.pi/2,4,"Y"),1),
    (base,"07_stand_base_mm.stl",Matrix.Identity(4),1),
    (bezel,"08_front_bezel_whole_FRONT_DOWN_mm.stl",Matrix.Rotation(math.pi,4,"X") @ T.inverted(),1),
    (bezel_halves[0],"09_front_bezel_left_FRONT_DOWN_mm.stl",Matrix.Rotation(math.pi,4,"X") @ T.inverted(),1),
    (bezel_halves[1],"10_front_bezel_right_FRONT_DOWN_mm.stl",Matrix.Rotation(math.pi,4,"X") @ T.inverted(),1),
    (coupon,"11_PRINT_FIRST_M3_and_brass_coupon_mm.stl",Matrix.Identity(4),1),
]: flat.append((obj,export_part(obj,file,transform,qty)))
(OUT/"网格与导出检查.json").write_text(json.dumps(checks,ensure_ascii=False,indent=2),encoding="utf-8")

# The main assembly shows the external bridges without invading screen space.
for y in (-55,55):
    obj=bpy.data.objects.new("背面连接片_已装配_"+str(y),bridge.data)
    parts_collection.objects.link(obj)
    obj.matrix_world=T @ Matrix.Translation((0,y,-4))
for obj in [*halves,bridge,*bezel_halves,coupon]:
    parts_collection.objects.unlink(obj)
    temp.objects.link(obj)
    obj.hide_render=True
    obj.hide_set(True)

# Reference screen: all parts here are visual proxies, excluded from every STL.
reference=[]
screen=rounded("屏幕正面_包络示意_不打印",P["screen_envelope_xy"][0],P["screen_envelope_xy"][1]-20,2,FRONT-2,FRONT)
screen.data.transform(T)
screen.data.materials.append(black); reference.append(screen)
for x in (-pitchx/2,pitchx/2):
    for y in (-pitchy/2,pitchy/2):
        lug=rounded("屏幕安装耳_仅示意_不作为压紧尺寸",6,9,2,MOUNT,MOUNT+1,cx=x,cy=y)
        boolean(lug,[cylinder("reference_hole",1.6,MOUNT-1,MOUNT+2,cx=x,cy=y)])
        lug.data.transform(T); lug.data.materials.append(black); reference.append(lug)
        brass=cylinder("现有10mm铜柱_外形仅示意_不打印",3.2,P["floor"],MOUNT,cx=x,cy=y,sides=6)
        brass.data.transform(T); brass.data.materials.append(orange); reference.append(brass)
        head=cylinder("屏幕原有M3螺钉头_仅示意_不打印",3,MOUNT+1,MOUNT+4,cx=x,cy=y)
        head.data.transform(T); head.data.materials.append(metal); reference.append(head)
display=rounded("223x125显示区_仅示意_不打印",223,125,1,FRONT+.05,FRONT+.15)
display.data.transform(T); display.data.materials.append(glass); reference.append(display)
# Restrained screen graphic, made in Blender geometry rather than a raster asset.
for i,width in enumerate((130,84,104)):
    line=rounded("显示内容_仅示意",width,1.5,.7,FRONT+.17,FRONT+.2,cy=8-i*9)
    line.data.transform(T); line.data.materials.append(white); reference.append(line)
for obj in reference:
    for col in list(obj.users_collection): col.objects.unlink(obj)
    ref_collection.objects.link(obj)
ref_collection["说明"]="照片仅确定正面尺寸；此屏幕不是背部PCB或连接器的精确模型"

# Indicative bolts show the four detachable rear mounting points.
for x in (-stand_x,stand_x):
    for y in (-stand_y,stand_y):
        bolt=cylinder("M4支架螺钉_仅示意_不打印",3.8,-8.4,-6,cx=x,cy=y,sides=6)
        bolt.data.transform(STAND_T); bolt.data.materials.append(metal)
        for col in list(bolt.users_collection): col.objects.unlink(bolt)
        ref_collection.objects.link(bolt)

# Print layout scene carries already oriented, independently checked meshes.
print_scene=bpy.data.scenes.new("03_打印摆放_左右分体方案")
print_scene.unit_settings.system="METRIC"; print_scene.unit_settings.scale_length=.001
print_scene.unit_settings.length_unit="MILLIMETERS"
positions=[(-240,200),(-100,200),(-250,-10),(140,-20),(-40,-20),(110,200),(-180,-210),(-40,-210),(-250,-85)]
for (source,mesh),(x,y) in zip(flat[1:7]+flat[8:],positions):
    obj=bpy.data.objects.new(source.name+"_打印方向",mesh)
    print_scene.collection.objects.link(obj)
    obj.location=(x,y,0)
    if source==bridge:
        extra=bpy.data.objects.new("连接片_第2件",mesh)
        print_scene.collection.objects.link(extra); extra.location=(x,y-25,0)

def setup_camera(scn,name,position,target,ortho):
    camera_data=bpy.data.cameras.new(name)
    camera=bpy.data.objects.new(name,camera_data); scn.collection.objects.link(camera)
    camera.location=position
    camera.rotation_euler=(Vector(target)-camera.location).to_track_quat("-Z","Y").to_euler()
    camera_data.type="ORTHO"; camera_data.ortho_scale=ortho
    camera_data.clip_start=.1; camera_data.clip_end=10000
    scn.camera=camera
    return camera

def render_settings(scn):
    scn.render.engine="BLENDER_WORKBENCH"
    scn.render.resolution_x=1600; scn.render.resolution_y=1200
    scn.render.resolution_percentage=100
    scn.render.image_settings.file_format="PNG"
    scn.display.shading.light="STUDIO"
    scn.display.shading.studio_light="paint.sl"
    scn.display.shading.color_type="MATERIAL"
    scn.display.shading.show_shadows=True
    scn.display.shading.show_cavity=True
    scn.display.shading.cavity_type="BOTH"
    scn.display.shading.curvature_ridge_factor=1.2
    scn.display.shading.curvature_valley_factor=1.1
    scn.display.shading.show_object_outline=True
    scn.display.shading.background_type="WORLD"
    world=bpy.data.worlds.new(scn.name+"_背景")
    world.color=(.22,.25,.29); scn.world=world
    scn.view_settings.view_transform="Standard"

render_settings(scene)
setup_camera(scene,"整机_前斜视",(340,-470,285),(0,10,95),365)
scene.render.filepath=str(OUT/"01_整机装配预览.png")
bpy.ops.render.render(write_still=True,scene=scene.name)

back=bpy.data.scenes.new("02_背面散热与可拆支架")
back.unit_settings.system="METRIC"; back.unit_settings.scale_length=.001
back.unit_settings.length_unit="MILLIMETERS"
back.collection.children.link(parts_collection)
back.collection.children.link(ref_collection)
render_settings(back)
setup_camera(back,"背面_散热视图",(320,450,280),(0,12,93),370)
back.render.filepath=str(OUT/"02_背面散热与支架预览.png")
bpy.ops.render.render(write_still=True,scene=back.name)

render_settings(print_scene)
print_scene.view_layers[0].update()
points=[obj.matrix_world @ vertex.co for obj in print_scene.objects if obj.type=="MESH" for vertex in obj.data.vertices]
lo=Vector([min(v[k] for v in points) for k in range(3)])
hi=Vector([max(v[k] for v in points) for k in range(3)])
center=(lo+hi)/2
camera=setup_camera(print_scene,"打印零件",center+Vector((280,-570,820)),center,650)
q=camera.rotation_euler.to_quaternion()
right=q @ Vector((1,0,0)); up=q @ Vector((0,1,0))
rx=[(p-center).dot(right) for p in points]
uy=[(p-center).dot(up) for p in points]
offset=right*((min(rx)+max(rx))/2)+up*((min(uy)+max(uy))/2)
camera.location+=offset
camera.data.ortho_scale=max(max(rx)-min(rx),(max(uy)-min(uy))*4/3)*1.12
print_scene.render.filepath=str(OUT/"03_打印零件与摆放方向.png")
bpy.ops.render.render(write_still=True,scene=print_scene.name)

# An unobstructed inside view makes the screw pillars / joining lands reviewable.
inside=bpy.data.scenes.new("04_底壳内侧结构")
inside.unit_settings.system="METRIC"; inside.unit_settings.scale_length=.001
inside.unit_settings.length_unit="MILLIMETERS"
inside_obj=bpy.data.objects.new("底壳内侧_不重复打印",shell.data)
inside.collection.objects.link(inside_obj)
render_settings(inside)
setup_camera(inside,"内侧结构",(240,-330,450),(0,0,5),315)
inside.render.filepath=str(OUT/"04_底壳内部结构.png")
bpy.ops.render.render(write_still=True,scene=inside.name)

# Save a useful material-shaded opening view, with no selected outline clutter.
if bpy.context.window: bpy.context.window.scene=scene
for obj in bpy.context.selected_objects: obj.select_set(False)
for screen_ui in bpy.data.screens:
    for area in screen_ui.areas:
        if area.type=="VIEW_3D":
            area.spaces.active.shading.type="SOLID"
            area.spaces.active.shading.color_type="MATERIAL"
            area.spaces.active.clip_start=.1
            area.spaces.active.clip_end=10000
            area.spaces.active.region_3d.view_distance=410
            area.spaces.active.region_3d.view_location=(0,10,96)
            area.spaces.active.region_3d.view_rotation=scene.camera.rotation_euler.to_quaternion()
bpy.data.collections.remove(temp)
bpy.context.preferences.filepaths.save_version=0
bpy.ops.wm.save_as_mainfile(filepath=str(OUT/"屏幕底壳_M3铜柱安装版.blend"))
print("BLENDER_SCREEN_ENCLOSURE_COMPLETE",flush=True)
