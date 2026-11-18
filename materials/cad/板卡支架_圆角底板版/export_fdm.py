import bpy, bmesh, json, math, struct, hashlib
from pathlib import Path
from mathutils import Matrix, Vector

output=Path(__file__).resolve().parent/'FDM打印包'
output.mkdir(exist_ok=True)
source=Path(bpy.data.filepath)
bpy.context.view_layer.update()
depsgraph=bpy.context.evaluated_depsgraph_get()
mm=bpy.context.scene.unit_settings.scale_length*1000
specs=[
    ('通风底座 · 01_base','01_base_mm.stl',0,(215,126,5),'底板，底面朝下'),
    ('上盖 · 分解位置','02_cover_top_down_mm.stl',180,(213.8,124.8,119),'上盖，外顶面朝下，开口朝上'),
    ('SFP 端固定臂','03_SFP_arm_flat_mm.stl',-90,(32.5,108.3,24),'SFP端固定臂，大平面朝下'),
    ('供电端固定臂','04_power_arm_flat_mm.stl',-90,(83.5,108.3,24),'供电端固定臂，大平面朝下'),
]
report={'source':source.name,'source_sha256':hashlib.sha256(source.read_bytes()).hexdigest(),
    'units':'STL numerical coordinates are millimeters; import as mm at 100% scale',
    'changes':'Rigid transforms and unit conversion only; no mesh repair, geometry redesign or source overwrite',
    'limitations':'Not sliced for a specific printer; no physical print or fit validation; no exhaustive self-intersection test',
    'parts':[]}
preview=bpy.data.scenes.new('FDM orientations')
preview.unit_settings.system='METRIC'
preview.unit_settings.scale_length=.001
preview.unit_settings.length_unit='MILLIMETERS'
objects=[]
for i,(name,filename,angle,expected,description) in enumerate(specs):
    obj=bpy.data.objects[name]
    ev=obj.evaluated_get(depsgraph)
    mesh=ev.to_mesh()
    bm=bmesh.new()
    bm.from_mesh(mesh)
    bm.transform(ev.matrix_world)
    for v in bm.verts: v.co*=mm
    bm.transform(Matrix.Rotation(math.radians(angle),4,'X'))
    low=Vector([min(v.co[k] for v in bm.verts) for k in range(3)])
    high=Vector([max(v.co[k] for v in bm.verts) for k in range(3)])
    dimensions=high-low
    assert all(abs(dimensions[k]-expected[k])<.002 for k in range(3)), (name,list(dimensions))
    shift=Vector((-(high.x+low.x)/2,-(high.y+low.y)/2,-low.z))
    for v in bm.verts: v.co+=shift
    bm.normal_update()
    assert all(e.is_manifold and e.is_contiguous for e in bm.edges), name
    assert bm.calc_volume(signed=True)>0,name
    review_mesh=bpy.data.meshes.new(filename)
    bm.to_mesh(review_mesh)
    review_obj=bpy.data.objects.new(description,review_mesh)
    preview.collection.objects.link(review_obj)
    review_obj.location=Vector(((i%2)*270,(i//2)*200,0))
    review_obj.color=[(.33,.58,.76,1),(.62,.64,.67,1),(.88,.55,.24,1),(.88,.55,.24,1)][i]
    objects.append(review_obj)
    bmesh.ops.triangulate(bm,faces=list(bm.faces))
    bm.normal_update()
    path=output/filename
    with path.open('wb') as f:
        f.write(b'FDM part; coordinate units: millimeters; import at 100%'.ljust(80,b'\x00'))
        f.write(struct.pack('<I',len(bm.faces)))
        for face in bm.faces:
            values=[*face.normal]
            for v in face.verts: values.extend(v.co)
            f.write(struct.pack('<12fH',*values,0))
    # Independently read STL bytes back to check written scale, bed position and mesh closure.
    data=path.read_bytes()
    count=struct.unpack_from('<I',data,80)[0]
    assert len(data)==84+50*count
    coords=[]
    edge_counts={}
    signed_volume=0.
    for record in struct.iter_unpack('<12fH',data[84:]):
        tri=[tuple(record[k:k+3]) for k in (3,6,9)]
        coords.extend(tri)
        a,b,c=[Vector(p) for p in tri]
        signed_volume+=a.dot(b.cross(c))/6
        for k in range(3):
            edge=tuple(sorted((tri[k],tri[(k+1)%3])))
            edge_counts[edge]=edge_counts.get(edge,0)+1
    written_low=[min(p[k] for p in coords) for k in range(3)]
    written_high=[max(p[k] for p in coords) for k in range(3)]
    written_dims=[written_high[k]-written_low[k] for k in range(3)]
    assert all(abs(written_dims[k]-expected[k])<.002 for k in range(3))
    assert abs(written_low[2])<.0001
    assert all(n==2 for n in edge_counts.values()),filename
    assert signed_volume>0,filename
    report['parts'].append({'source_object':name,'file':filename,'quantity':1,'description':description,
        'rotation_x_degrees':angle,'dimensions_mm':written_dims,'min_z_mm':written_low[2],
        'triangles':count,'stl_edge_incidence_2':True,'signed_volume_mm3':signed_volume,
        'sha256':hashlib.sha256(data).hexdigest()})
    bm.free()
    ev.to_mesh_clear()
(output/'export_checks.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
print('EXPORTED_4_STL_WITH_READBACK_CHECKS',flush=True)

preview.render.engine='BLENDER_WORKBENCH'
preview.display.shading.light='STUDIO'
preview.display.shading.color_type='OBJECT'
preview.display.shading.show_shadows=True
preview.display.shading.show_cavity=True
preview.display.shading.cavity_type='BOTH'
preview.display.shading.show_object_outline=True
preview.display.shading.background_type='WORLD'
preview.world=bpy.data.worlds.new('FDM preview world')
preview.world.color=(.12,.12,.12)
preview.render.resolution_x=1500
preview.render.resolution_y=1100
preview.render.resolution_percentage=100
preview.render.image_settings.file_format='PNG'
camera_data=bpy.data.cameras.new('FDM preview camera')
camera=bpy.data.objects.new('FDM preview camera',camera_data)
preview.collection.objects.link(camera)
preview.camera=camera
camera_data.type='ORTHO'
camera_data.clip_start=.01
camera_data.clip_end=10000
preview.view_layers[0].update()
points=[o.matrix_world@v.co for o in objects for v in o.data.vertices]
low=Vector([min(v[k] for v in points) for k in range(3)])
high=Vector([max(v[k] for v in points) for k in range(3)])
center=(low+high)/2
radius=(high-low).length/2
direction=Vector((.7,-1,1.6)).normalized()
camera.location=center+direction*radius*5
camera.rotation_euler=(-direction).to_track_quat('-Z','Y').to_euler()
camera_data.ortho_scale=radius*2.7
preview.render.filepath=str(output/'打印方向示意.png')
bpy.ops.render.render(write_still=True,scene=preview.name)
print('FDM_PACKAGE_EXPORTED',flush=True)
