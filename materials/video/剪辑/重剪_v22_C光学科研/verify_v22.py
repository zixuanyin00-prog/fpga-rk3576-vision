"""Verify the replacement set, frame cadence, timing, sound and representative frames."""
from pathlib import Path
import hashlib,io,json,re,subprocess
from PIL import Image,ImageDraw,ImageFont
import build_v22 as b

p=Path(__file__).resolve().parent
def probe(file):
    return json.loads(subprocess.run(['ffprobe','-v','error','-show_streams','-show_format','-show_chapters','-of','json',str(file)],capture_output=True,text=True,encoding='utf8',check=True).stdout)

meta=probe(b.OUT);v=next(s for s in meta['streams'] if s['codec_type']=='video')
assert (v['width'],v['height'],v['avg_frame_rate'],v['nb_frames'])==(1920,1080,'60/1','10788')
assert abs(float(meta['format']['duration'])-179.8)<.001
audio=next(s for s in meta['streams'] if s['codec_type']=='audio')
assert audio['sample_rate']=='48000' and audio['channels']==2
rows=json.loads((p/'timeline.json').read_text('utf8'))
old=json.loads((b.OLD/'timeline.json').read_text('utf8'))
assert [(s['name'],s['start'],s['duration']) for s in rows]==[(s['name'],s['start'],s['duration']) for s in old]
render_reports=[]
for s in rows:
    if s['name'] in b.NAMES:
        file=Path(s['file']);m=probe(file);vs=next(z for z in m['streams'] if z['codec_type']=='video')
        assert (vs['avg_frame_rate'],int(vs['nb_frames']))==('60/1',round(s['duration']*60))
        rr=json.loads(Path(str(file)+'.render.json').read_text('utf8'))
        assert rr['animation_time_step_seconds']==1/60
        render_reports.append(dict(scene=s['name'],frames=round(s['duration']*60),**rr))
    else:assert s['file']==next(o['file'] for o in old if o['name']==s['name'])
for file in ['captions.json','全片字幕.srt','配音生成记录.json','字幕配音逐句核对.json']:
    assert (p/file).read_bytes()==(b.OLD/file).read_bytes()
ass=(p/'final.ass').read_text('utf-8-sig')
assert not any(word in ass for word in ['提示词','引子','不表示','零漏检','完美筛选'])
assert all(word in ass for word in ['PH1A90','RK3576','PCIe','1080p','60 FPS','50 ms','99.997%'])
subprocess.run([str(b.FF),'-v','error','-nostdin','-i',str(b.OUT),'-map','0:v','-map','0:a','-f','null','-'],check=True)
measure=subprocess.run([str(b.FF),'-hide_banner','-nostdin','-i',str(b.OUT),'-af','loudnorm=I=-18:TP=-1.5:LRA=9:print_format=json','-vn','-f','null','-'],capture_output=True,text=True,encoding='utf8',check=True).stderr
levels=json.loads(re.findall(r'\{\s*"input_i"[\s\S]*?\}',measure)[-1])
assert float(levels['input_tp'])<-.5
moments=[(.95,'Cover'),(6.75,'01 Hardware'),(31.1,'02 Console'),(42,'ISP'),(65,'Few shot'),(68.3,'03 Recognition'),(71.8,'PCIe / compression'),(75.8,'Same camera / three frames'),(79.3,'Trigger'),(83.5,'Dimensions'),(89,'Edge evidence'),(93.15,'04 Application'),(105,'Pose sorting'),(114,'HDMI performance'),(158,'Category sorting'),(177.4,'Highlights')]
sheet=Image.new('RGB',(1920,1192),'#cadce6');d=ImageDraw.Draw(sheet);font=ImageFont.truetype('C:/Windows/Fonts/msyh.ttc',19)
for i,(t,title) in enumerate(moments):
    data=subprocess.run([str(b.FF),'-v','error','-ss',str(t),'-i',str(b.OUT),'-frames:v','1','-vf','scale=480:270','-f','image2pipe','-vcodec','png','-'],capture_output=True,check=True).stdout
    x,y=i%4*480,i//4*298;sheet.paste(Image.open(io.BytesIO(data)),(x,y+28));d.text((x+6,y+3),f'{t:g}s {title}',font=font,fill='#183747')
sheet.save(p/'全片视觉核对.jpg',quality=96)
report=dict(output=str(b.OUT),duration_seconds=179.8,frames=10788,fps=60,replaced_code_scenes=13,replaced_code_seconds=48,application_seconds=80.3,existing_footage_sources_unchanged=True,narration_and_caption_content_unchanged=True,full_decode_passed=True,audio_levels=levels,render_reports=render_reports,size_bytes=b.OUT.stat().st_size)
(p/'成片核对.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),'utf8')
print(json.dumps({k:v for k,v in report.items() if k!='render_reports'},ensure_ascii=True),flush=True)
