"""Remove editorial playback overlays and redundant notes from the selected film."""
from pathlib import Path
import argparse,asyncio,importlib.util,json,re,shutil,subprocess

P=Path(__file__).resolve().parent;OLD=P.parent/'重剪_v27_片头参赛信息'
spec=importlib.util.spec_from_file_location('v28_base',P.parent/'重剪_v26_3600件每分钟/build_v26.py');parent=importlib.util.module_from_spec(spec);spec.loader.exec_module(parent)
b=parent.b;b.P=P;b.OLD=OLD;b.OUT=P/'FPGA视觉系统_B简洁标注_v28.mp4';b.tr.P=P;b.mixbase.P=P;b.mixbase.OUT=b.OUT
original_captions=b.captions


def captions():
    original_captions();lines=[];removed=[]
    for line in (P/'final.ass').read_text('utf-8-sig').splitlines():
        if not line.startswith('Dialogue:'):lines.append(line);continue
        f=line.split(',',9);layer=int(f[0].split(':')[1]);body=re.sub(r'\{[^}]*\}','',f[9])
        is_speed=re.fullmatch(r'(?:\d+(?:\.\d+)?\s*[×xX]|[×xX]\s*\d+(?:\.\d+)?)(?:\s*(?:慢放|倍速|快进))?',body)
        redundant=body in ['姿态检测　HDMI实时画面','多目标并行识别　分拣功能未开启']
        if layer!=1 and (is_speed or '慢放' in body or '倍速' in body or redundant):removed.append(dict(layer=layer,start=f[1],end=f[2],text=body));continue
        lines.append(line)
    ass='\n'.join(lines)+'\n';assert not any(x in ass for x in ['慢放','倍速','快进'])
    (P/'final.ass').write_text(ass,'utf-8-sig');(P/'已删标注.json').write_text(json.dumps(removed,ensure_ascii=False,indent=2),'utf8')
    print(f'Removed {len(removed)} playback/redundant overlays',flush=True)


b.captions=captions


def prepare():
    rows=json.loads((OLD/'timeline.json').read_text('utf8'))
    for s in rows:s.pop('speed_label',None)
    dimension=next(s for s in rows if s['name']=='dimensions');dimension['file']=str(P/'scenes/dimensions.mp4');dimension['source']={'renderer':str(P/'evidence_scenes_v28.cjs'),'basis':'same original pixel boxes; editorial figure-number note removed'}
    (P/'timeline.json').write_text(json.dumps(rows,ensure_ascii=False,indent=2),'utf8');(P/'evidence_render_plan.json').write_text(json.dumps([dimension],ensure_ascii=False,indent=2),'utf8')
    plan=json.loads((OLD/'voice_plan.json').read_text('utf8'));words={30:'白色电阻被准确吹出。',28:'绿色确认框随工件位置更新。'}
    for cue in plan:
        if cue['id'] in words:cue['spoken_text']=words[cue['id']];cue.pop('reuse_id',None)
    (P/'voice_plan.json').write_text(json.dumps(plan,ensure_ascii=False,indent=2),'utf8');shutil.copy2(OLD/'chapters.ffmeta',P/'chapters.ffmeta')
    assert sum(round(s['duration']*60) for s in rows)==10788
    print('Prepared: playback notes removed; all timing retained',flush=True)


def assemble():
    rows=json.loads((P/'timeline.json').read_text('utf8'));previous=json.loads((OLD/'transition_parts/verification.json').read_text('utf8'))
    parts=P/'transition_parts';parts.mkdir(exist_ok=True);index=next(i for i,s in enumerate(rows) if s['name']=='dimensions')
    changed={f'body_{index:02}.mp4',f'bridge_{index-1:02}.mp4',f'bridge_{index:02}.mp4'};order=[]
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
    (parts/'verification.json').write_text(json.dumps(dict(frames=10788,duration=179.8,reused_parts=len(order)-3,changed_parts=sorted(changed),parts=[dict(file=str(f),frames=r['frames']) for f,r in zip(order,previous['parts'])]),ensure_ascii=False,indent=2),'utf8')




if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('stage',choices=['prepare','voice','compose','assemble','final']);a=parser.parse_args()
    if a.stage=='voice':asyncio.run(b.voice())
    elif a.stage=='compose':b.compose()
    elif a.stage=='final':b.mixbase.final()
    else:globals()[a.stage]()
