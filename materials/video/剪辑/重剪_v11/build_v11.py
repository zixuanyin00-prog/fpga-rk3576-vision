"""Replace the result split screen with one continuous centered close view."""
from pathlib import Path
import importlib.util,json,shutil,subprocess

P=Path(__file__).resolve().parent
OLD=P.parent/'重剪_v10'
FF=P.parent.parent/'dataflow_film_20261028/.tools/imageio_ffmpeg/binaries/ffmpeg-win-x86_64-v7.1.exe'
SOURCE=P.parent/'类别分拣.mp4'
OLD_VIDEO=OLD/'FPGA视觉系统_分拣规则与动画优化_v10.mp4'
OUT=P/'FPGA视觉系统_单画面结果展示_v11.mp4'
spec=importlib.util.spec_from_file_location('transition_tools',OLD/'add_transitions.py')
tr=importlib.util.module_from_spec(spec);spec.loader.exec_module(tr);tr.P=P
timeline=json.loads((OLD/'timeline.json').read_text(encoding='utf8'))
prior=json.loads((OLD/'成片核对.json').read_text(encoding='utf8'))
assert OLD_VIDEO.stat().st_size==prior['size_bytes']
index=next(i for i,s in enumerate(timeline) if s['name']=='app_compare')
scene=timeline[index]
assert scene['duration']==12
clip=P/'app_compare.mp4'
graph=('[0:v]setpts=PTS-STARTPTS,crop=2000:1320:80:520,scale=1300:858,setsar=1,fps=60,'
       'pad=1920:1080:310:20:color=0x0d1d29,'
       'drawbox=x=308:y=18:w=1304:h=862:color=0x355768:t=2[v]')
with (P/'single_view.log').open('w',encoding='utf8') as log:
    subprocess.run([str(FF),'-y','-hide_banner','-nostdin','-ss','0','-t','12','-i',str(SOURCE),'-filter_complex',graph,'-map','[v]','-frames:v','720',*tr.ENC,str(clip)],stdout=log,stderr=log,check=True)
assert tr.probe(clip,720)
timeline[index]['file']=str(clip)
timeline[index]['source']=dict(kind='single_view',file=str(SOURCE),start=0,length=12,crop='crop=2000:1320:80:520',scale='scale=1300:858',x=310,y=20)
(P/'timeline.json').write_text(json.dumps(timeline,ensure_ascii=False,indent=2),encoding='utf8')
for name in ['final.ass','全片字幕.srt','captions.json','分拣规则标识.json']:
    shutil.copyfile(OLD/name,P/name)
parts=P/'transition_parts';parts.mkdir(exist_ok=True)
replacements={}
def signature(path):
    s=path.stat()
    return dict(file=str(path),size=s.st_size,mtime_ns=s.st_mtime_ns)
body=parts/f'body_{index:02}.mp4'
frames=720-24
tr.render(body,['-i',str(clip)],'[0:v]trim=start_frame=12:end_frame=708,setpts=PTS-STARTPTS,setsar=1,fps=60[v]',frames,dict(kind='single_view_body',source=signature(clip)),parts)
replacements[body.name]=body
for i in [index-1,index]:
    left=Path(timeline[i]['file']);right=Path(timeline[i+1]['file']);n=round(timeline[i]['duration']*60)
    bridge=parts/f'bridge_{i:02}.mp4'
    tr.render(bridge,['-i',str(left),'-i',str(right)],tr.bridge_graph(n),24,dict(kind='single_view_bridge',left=signature(left),right=signature(right)),parts)
    replacements[bridge.name]=bridge
previous=json.loads((OLD/'transition_parts/verification.json').read_text(encoding='utf8'))
order=[replacements.get(Path(x['file']).name,Path(x['file'])) for x in previous['parts']]
concat=parts/'concat.txt'
concat.write_text(''.join("file '"+f.as_posix()+"'\n" for f in order),encoding='utf8')
with (parts/'assemble.log').open('w',encoding='utf8') as log:
    subprocess.run([str(FF),'-y','-hide_banner','-nostdin','-f','concat','-safe','0','-i',str(concat),'-map','0:v:0','-c:v','copy','-an','-movflags','+faststart',str(P/'video_transitions.mp4')],stdout=log,stderr=log,check=True)
assert tr.probe(P/'video_transitions.mp4',9738)
with (P/'final_render.log').open('w',encoding='utf8') as log:
    subprocess.run([str(FF),'-y','-hide_banner','-nostdin','-i','video_transitions.mp4','-i',str(OLD_VIDEO),'-map','0:v:0','-map','1:a:0','-vf','ass=final.ass',*tr.ENC[1:],'-c:a','copy','-t','162.3','-movflags','+faststart',str(OUT)],cwd=P,stdout=log,stderr=log,check=True)
assert tr.probe(OUT,9738)
report=dict(output=str(OUT),duration_seconds=162.3,width=1920,height=1080,export_fps=60,video_frames=9738,changed_scene='app_compare',changed_start=scene['start'],changed_duration=12,single_view=True,crop='2000:1320:80:520',source_playback_rate=1,no_freeze=True,subtitles_unchanged=True,audio_stream_copied_from=str(OLD_VIDEO),size_bytes=OUT.stat().st_size)
(P/'成片核对.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf8')
print(json.dumps(report,ensure_ascii=True),flush=True)
