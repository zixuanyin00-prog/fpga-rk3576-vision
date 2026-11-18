"""Update the requested speed in narration, subtitles and the B ending card."""
from pathlib import Path
import argparse,asyncio,importlib.util,json,shutil,subprocess

P=Path(__file__).resolve().parent;OLD=P.parent/'重剪_v25_增稳与口播修订'
spec=importlib.util.spec_from_file_location('v26_base',P.parent/'重剪_v24_清晰关联与慢放/build_v24.py')
b=importlib.util.module_from_spec(spec);spec.loader.exec_module(b)
b.P=P;b.OLD=OLD;b.OUT=P/'FPGA视觉系统_B分拣速度3600_v26.mp4';b.tr.P=P;b.mixbase.P=P;b.mixbase.OUT=b.OUT
previous_normalize=b.normalize
b.normalize=lambda text:previous_normalize(text).replace('三千六百','3600')


def prepare():
    rows=json.loads((OLD/'timeline.json').read_text('utf8'));end=next(x for x in rows if x['name']=='end')
    end['file']=str(P/'scenes/end.mp4');end['source']={'renderer':str(P/'scenes_b_v26.cjs'),'geometry':str(P.parent/'重剪_v21/geometry_b.cjs'),'native_render_fps':60,'speed_workpieces_per_minute':3600}
    (P/'timeline.json').write_text(json.dumps(rows,ensure_ascii=False,indent=2),'utf8')
    (P/'render_plan.json').write_text(json.dumps([end],ensure_ascii=False,indent=2),'utf8')
    plan=json.loads((OLD/'voice_plan.json').read_text('utf8'));cue=next(x for x in plan if x['id']==21)
    cue['spoken_text']='实测分拣速度达到每分钟三千六百件。';cue.pop('reuse_id',None)
    (P/'voice_plan.json').write_text(json.dumps(plan,ensure_ascii=False,indent=2),'utf8');shutil.copy2(OLD/'chapters.ffmeta',P/'chapters.ffmeta')
    assert sum(round(x['duration']*60) for x in rows)==10788
    print('Prepared: speed only; timeline remains 179.8 seconds',flush=True)


def assemble():
    rows=json.loads((P/'timeline.json').read_text('utf8'));previous=json.loads((OLD/'transition_parts/verification.json').read_text('utf8'))
    parts=P/'transition_parts';parts.mkdir(exist_ok=True);index=next(i for i,s in enumerate(rows) if s['name']=='end')
    changed={f'body_{index:02}.mp4',f'bridge_{index-1:02}.mp4'};order=[]
    for item in previous['parts']:
        oldfile=Path(item['file']);name=oldfile.name
        if name not in changed:order.append(oldfile);continue
        i=int(oldfile.stem.split('_')[1]);s=rows[i];source=Path(s['file']);stat=source.stat();record=dict(file=str(source),size=stat.st_size,mtime_ns=stat.st_mtime_ns)
        frames=round(s['duration']*60);out=parts/name
        if name.startswith('body'):
            a=12 if i else 0;z=frames if i==len(rows)-1 else frames-12
            graph=f'[0:v]trim=start_frame={a}:end_frame={z},setpts=PTS-STARTPTS,setsar=1,fps=60[v]'
            signature=dict(version=1,kind='body',source=record,start=a,end=z,fps=60);inputs=['-i',str(source)]
        else:
            following=Path(rows[i+1]['file']);stat=following.stat();right=dict(file=str(following),size=stat.st_size,mtime_ns=stat.st_mtime_ns)
            graph=b.tr.bridge_graph(frames);signature=dict(version=2,kind='bridge',left=record,right=right,half_frames=12,blend_seconds=.4,fps=60);inputs=['-i',str(source),'-i',str(following)]
        b.tr.render(out,inputs,graph,item['frames'],signature,parts);order.append(out)
    concat=parts/'concat.txt';concat.write_text(''.join("file '"+x.as_posix().replace("'","'\\''")+"'\n" for x in order),'utf8')
    with (parts/'assemble.log').open('w',encoding='utf8') as log:
        subprocess.run([str(b.FF),'-y','-hide_banner','-nostdin','-f','concat','-safe','0','-i',str(concat),'-c:v','copy','-an','-movflags','+faststart',str(P/'video_clean.mp4')],stdout=log,stderr=log,check=True)
    assert b.tr.probe(P/'video_clean.mp4',10788)
    (parts/'verification.json').write_text(json.dumps(dict(frames=10788,duration=179.8,reused_parts=len(order)-2,changed_parts=sorted(changed),parts=[dict(file=str(f),frames=r['frames']) for f,r in zip(order,previous['parts'])]),ensure_ascii=False,indent=2),'utf8')


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('stage',choices=['prepare','voice','compose','assemble','final']);a=parser.parse_args()
    if a.stage=='voice':asyncio.run(b.voice())
    elif a.stage=='compose':b.compose()
    elif a.stage=='final':b.mixbase.final()
    else:globals()[a.stage]()
