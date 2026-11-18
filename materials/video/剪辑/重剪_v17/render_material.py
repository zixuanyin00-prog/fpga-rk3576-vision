"""Normal-speed, single-view presentation of the two pre-experiment materials."""
from pathlib import Path
import json,subprocess

P=Path(__file__).resolve().parent
SOURCE=P.parent/'重剪_v11/app_compare.mp4'
FF=P.parent.parent/'dataflow_film_20261028/.tools/imageio_ffmpeg/binaries/ffmpeg-win-x86_64-v7.1.exe'
segments=[dict(model='473',start=2.5,length=3),dict(model='1004',start=7.6,length=3)]
args=[];graph=[]
for i,s in enumerate(segments):
    args+=['-ss',str(s['start']),'-t','3.05','-i',str(SOURCE)]
    graph.append(f'[{i}:v]trim=start_frame=0:end_frame=180,setpts=PTS-STARTPTS,setsar=1[v{i}]')
graph.append('[v0][v1]concat=n=2:v=1:a=0[v]')
out=P/'material_prepare.mp4'
with (P/'material_render.log').open('w',encoding='utf8') as log:
    subprocess.run([str(FF),'-y','-hide_banner','-nostdin',*args,'-filter_complex',';'.join(graph),'-map','[v]',
        '-frames:v','360','-an','-c:v','h264_nvenc','-preset','p5','-cq','19','-b:v','0',
        '-pix_fmt','yuv420p','-r','60','-video_track_timescale','60000','-movflags','+faststart',str(out)],stdout=log,stderr=log,check=True)
provenance=dict(kind='normal_speed_single_view_material_montage',source=str(SOURCE),
    original_source=str(P.parent/'类别分拣.mp4'),original_crop='2000:1320:80:520',
    layout='Existing v11 single view retained: scaled1300x858 at310,20, no split screen',
    segments=segments,duration=6,frames=360,export_fps=60,playback_speed=1,
    frame_holds=False,description='Pre-experiment materials; 473 followed by1004, not sorted output')
(P/'source_material.json').write_text(json.dumps(provenance,ensure_ascii=False,indent=2),encoding='utf8')
print('material_prepare.mp4: 6s / 360 frames / 1x',flush=True)
