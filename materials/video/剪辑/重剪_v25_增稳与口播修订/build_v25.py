"""Two local edits to v24; preserve all authored timing and other sources."""
from pathlib import Path
import argparse,asyncio,hashlib,importlib.util,json,shutil,subprocess

P=Path(__file__).resolve().parent
OLD=P.parent/'重剪_v24_清晰关联与慢放'
spec=importlib.util.spec_from_file_location('v25_base',OLD/'build_v24.py')
b=importlib.util.module_from_spec(spec);spec.loader.exec_module(b)
b.P=P;b.OLD=OLD;b.OUT=P/'FPGA视觉系统_B增稳与口播修订_v25.mp4'
b.tr.P=P;b.mixbase.P=P;b.mixbase.OUT=b.OUT


def prepare():
    rows=json.loads((OLD/'timeline.json').read_text('utf8'))
    s=next(s for s in rows if s['name']=='pose_proof')
    s['file']=str(P/'scenes/pose_proof.mp4');s['speed_label']='1.5×'
    s['source']=dict(file=str(P.parent/'姿态分拣.mp4'),start=.3,length=9,playback_speed=1.5,crop='crop=3080:1732:340:60',stabilization=dict(method='two-pass FFmpeg vidstab',smoothing=35,optzoom=1,zoom=2))
    (P/'timeline.json').write_text(json.dumps(rows,ensure_ascii=False,indent=2),'utf8')
    plan=json.loads((OLD/'voice_plan.json').read_text('utf8'))
    cue=next(x for x in plan if x['id']==30)
    cue['spoken_text']='慢放可见，白色电阻被准确吹出。';cue.pop('reuse_id',None)
    (P/'voice_plan.json').write_text(json.dumps(plan,ensure_ascii=False,indent=2),'utf8')
    shutil.copy2(OLD/'chapters.ffmeta',P/'chapters.ffmeta')
    assert sum(round(s['duration']*60) for s in rows)==10788
    print('Prepared: only pose_proof and cue 30 changed; 179.8 seconds',flush=True)


def footage():
    s=next(x for x in json.loads((P/'timeline.json').read_text('utf8')) if x['name']=='pose_proof')
    src=s['source'];out=Path(s['file']);out.parent.mkdir(exist_ok=True)
    marker=out.with_suffix('.source.json')
    signature=dict(source=src,frames=360)
    if marker.exists() and json.loads(marker.read_text('utf8'))==signature and b.tr.probe(out,360):return
    common=[str(b.FF),'-y','-hide_banner','-nostdin','-ss',str(src['start']),'-t',str(src['length']),'-i',src['file']]
    pre=src['crop']+',scale=1920:1080:flags=lanczos,setsar=1'
    with (P/'vidstab_detect.log').open('w',encoding='utf8') as log:
        subprocess.run([*common,'-an','-vf',pre+',vidstabdetect=shakiness=7:accuracy=15:stepsize=8:mincontrast=0.2:result=pose_proof.trf','-f','null','-'],cwd=P,stdout=log,stderr=log,check=True)
    vf=pre+',vidstabtransform=input=pose_proof.trf:smoothing=35:optzoom=1:zoom=2:interpol=bicubic,setpts=(PTS-STARTPTS)/1.5,fps=60,drawbox=x=0:y=930:w=iw:h=150:color=0x0d1d29@0.65:t=fill,trim=end_frame=360,setpts=N/(60*TB)'
    with (P/'vidstab_transform.log').open('w',encoding='utf8') as log:
        subprocess.run([*common,'-vf',vf,'-frames:v','360',*b.tr.ENC,str(out)],cwd=P,stdout=log,stderr=log,check=True)
    assert b.tr.probe(out,360)
    marker.write_text(json.dumps(signature,ensure_ascii=False,indent=2),'utf8')
    print('Rendered 1.5x stabilized pose_proof: 360 frames',flush=True)


def assemble():
    rows=json.loads((P/'timeline.json').read_text('utf8'))
    previous=json.loads((OLD/'transition_parts/verification.json').read_text('utf8'))
    parts=P/'transition_parts';parts.mkdir(exist_ok=True)
    index=next(i for i,s in enumerate(rows) if s['name']=='pose_proof')
    changed={f'body_{index:02}.mp4',f'bridge_{index-1:02}.mp4',f'bridge_{index:02}.mp4'}
    order=[]
    for item in previous['parts']:
        oldfile=Path(item['file']);name=oldfile.name
        if name not in changed:
            assert b.tr.probe(oldfile,item['frames']);order.append(oldfile);continue
        i=int(oldfile.stem.split('_')[1]);s=rows[i];source=Path(s['file']);stat=source.stat()
        source_record=dict(file=str(source),size=stat.st_size,mtime_ns=stat.st_mtime_ns)
        frames=round(s['duration']*60);out=parts/name
        if name.startswith('body'):
            a,z=12,frames-12
            graph=f'[0:v]trim=start_frame={a}:end_frame={z},setpts=PTS-STARTPTS,setsar=1,fps=60[v]'
            signature=dict(version=1,kind='body',source=source_record,start=a,end=z,fps=60)
            inputs=['-i',str(source)]
        else:
            following=Path(rows[i+1]['file']);stat=following.stat()
            right=dict(file=str(following),size=stat.st_size,mtime_ns=stat.st_mtime_ns)
            graph=b.tr.bridge_graph(frames)
            signature=dict(version=2,kind='bridge',left=source_record,right=right,half_frames=12,blend_seconds=.4,fps=60)
            inputs=['-i',str(source),'-i',str(following)]
        b.tr.render(out,inputs,graph,item['frames'],signature,parts);order.append(out)
    concat=parts/'concat.txt'
    concat.write_text(''.join("file '"+x.as_posix().replace("'","'\\''")+"'\n" for x in order),'utf8')
    with (parts/'assemble.log').open('w',encoding='utf8') as log:
        subprocess.run([str(b.FF),'-y','-hide_banner','-nostdin','-f','concat','-safe','0','-i',str(concat),'-c:v','copy','-an','-movflags','+faststart',str(P/'video_clean.mp4')],stdout=log,stderr=log,check=True)
    assert b.tr.probe(P/'video_clean.mp4',10788)
    (parts/'verification.json').write_text(json.dumps(dict(frames=10788,duration=179.8,reused_parts=len(order)-3,changed_parts=sorted(changed),parts=[dict(file=str(f),frames=r['frames']) for f,r in zip(order,previous['parts'])]),ensure_ascii=False,indent=2),'utf8')
    print('Assembled: three transition parts updated; all other parts reused',flush=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('stage',choices=['prepare','footage','voice','compose','assemble','final']);a=parser.parse_args()
    if a.stage=='voice':asyncio.run(b.voice())
    elif a.stage=='compose':b.compose()
    elif a.stage=='final':b.mixbase.final()
    else:globals()[a.stage]()
