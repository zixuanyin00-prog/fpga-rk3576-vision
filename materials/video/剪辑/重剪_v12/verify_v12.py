from pathlib import Path
import io,json,re,shutil,subprocess
from PIL import Image,ImageDraw,ImageFont

P=Path(__file__).resolve().parent
FF=P.parent.parent/'dataflow_film_20261028/.tools/imageio_ffmpeg/binaries/ffmpeg-win-x86_64-v7.1.exe'
OUT=P/'FPGA视觉系统_章节与材料顺序修订_v12.mp4'
plan=json.loads((P/'配音计划.json').read_text(encoding='utf8'))
records=json.loads((P/'配音生成记录.json').read_text(encoding='utf8'))
caps=json.loads((P/'captions.json').read_text(encoding='utf8'))
timeline=json.loads((P/'timeline.json').read_text(encoding='utf8'))
assert len(records)==len(caps)==len(plan['segments'])
last=0
for r,c in zip(records,caps):
    words=r['spoken_text']
    for a,b in [('F P G A','FPGA'),('H D M I','HDMI'),('R G B','RGB'),('R K','RK'),('四七三','473'),('一零零四','1004'),('三五七六','3576')]:words=words.replace(a,b)
    assert re.sub(r'\s+','',words)==re.sub(r'\s+','',c['text'])
    assert r['start']>=last-.001
    assert r['start']+r['duration']<=r['end']+.025
    assert c['startMs']/1000<=r['start']+.001
    assert c['endMs']/1000>=r['start']+r['duration']-.001
    last=r['start']+r['duration']
for s in timeline:
    if s['name'].startswith('app_'):
        for part in s['source'].get('parts',[]):assert part.get('hold',0)==0
assert next(s['start'] for s in timeline if s['name']=='material_prepare') < next(s['start'] for s in timeline if s['name']=='app_hdmi')
assert timeline[0]['name']=='intro' and timeline[1]['name']=='chapter_board'
assert [s['name'] for s in timeline[2:5]]==['rk_board','fpga_board','control_board']
duration=round(sum(s['duration'] for s in timeline),4)
assert duration==plan['duration'] and duration<180
metadata=json.loads(subprocess.run([shutil.which('ffprobe'),'-v','error','-show_streams','-show_format','-show_chapters','-of','json',str(OUT)],capture_output=True,text=True,encoding='utf8',check=True).stdout)
expected_chapters=json.loads((P/'章节时间.json').read_text(encoding='utf8'))
assert len(metadata['chapters'])==len(expected_chapters)==5
for actual,expected in zip(metadata['chapters'],expected_chapters):
    assert actual['tags']['title']==expected['title']
    assert abs(float(actual['start_time'])-expected['start'])<.002
    assert abs(float(actual['end_time'])-expected['end'])<.002
assert timeline[-2]['name']=='app_collect'
for c in caps:
    if c['scene']=='material_prepare':assert '结果' not in c['text'] and '被剔除' not in c['text']
v=next(s for s in metadata['streams'] if s['codec_type']=='video')
a=next(s for s in metadata['streams'] if s['codec_type']=='audio')
assert (v['width'],v['height'],v['avg_frame_rate'],v['nb_frames'])==(1920,1080,'60/1',str(round(duration*60)))
assert abs(float(v['duration'])-duration)<.001
assert a['sample_rate']=='48000' and a['channels']==2
for s in timeline:
    if s['name']=='app_track':assert s['source']['start']>=0 and s['source']['start']+s['source']['length']<=7
    if s['name']=='app_execute':assert s['source']['start']>=30 and s['source']['start']+s['source']['length']<=56
    if s['name']=='material_prepare':assert s['source']['start']+s['source']['length']<=12
times=[(s['start']+s['duration']/2,s['name']) for s in timeline]
times.extend(((c['startMs']+c['endMs'])/2000,'cue '+str(c['id'])) for c in caps)
font=ImageFont.truetype('C:/Windows/Fonts/msyh.ttc',20)
for page in range((len(times)+11)//12):
    sheet=Image.new('RGB',(1920,1552),'#0d1d29');d=ImageDraw.Draw(sheet)
    for i,(t,label) in enumerate(times[page*12:(page+1)*12]):
        raw=subprocess.run([str(FF),'-v','error','-ss',str(t),'-i',str(OUT),'-frames:v','1','-vf','scale=640:360','-f','image2pipe','-vcodec','png','-'],capture_output=True,check=True).stdout
        x,y=(i%3)*640,(i//3)*388;sheet.paste(Image.open(io.BytesIO(raw)),(x,y+28));d.text((x+8,y+2),f'{int(t)//60:02}:{t%60:05.2f}  {label}',font=font,fill='white')
    sheet.save(P/f'成片审看_{page+1}.jpg',quality=94)
report=dict(output=str(OUT),duration_seconds=duration,under_three_minutes=True,dimensions=[1920,1080],export_fps=60,video_frames=round(duration*60),audio_sample_rate=48000,audio_channels=2,material_before_classification=True,board_order=['RK','FPGA','control'],chapters=5,subtitles=len(caps),subtitle_voice_words_match=True,voice_intervals_nonoverlapping=True,subtitles_cover_spoken_intervals=True,source_groups=5,new_source_files=6,filename_usable_windows_respected=True,console_seconds=sum(s['duration'] for s in timeline if s['name'].startswith('console_')),application_seconds=sum(s['duration'] for s in timeline if s['name'].startswith('app_')),actual_footage_freeze_removed=True,animation_seconds=sum(s['duration'] for s in timeline if s['name'].endswith('_short')),size_bytes=OUT.stat().st_size)
(P/'成片核对.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf8')
print(json.dumps(report,ensure_ascii=True),flush=True)
