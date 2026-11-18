"""Fast delivery checks and representative viewing; no per-workpiece audit."""
from pathlib import Path
import io,json,re,subprocess
from PIL import Image,ImageDraw,ImageFont
import build_v19 as b

p=Path(__file__).resolve().parent
meta=json.loads(subprocess.run(['ffprobe','-v','error','-show_streams','-show_format','-show_chapters','-of','json',str(b.OUT)],capture_output=True,text=True,encoding='utf8',check=True).stdout)
video=next(s for s in meta['streams'] if s['codec_type']=='video')
assert (video['width'],video['height'],video['avg_frame_rate'],video['nb_frames'])==(1920,1080,'60/1','10788')
assert abs(float(meta['format']['duration'])-179.8)<.001
plan=b.plan();rows=json.loads((p/'timeline.json').read_text(encoding='utf8'))
assert abs(sum(s['duration'] for s in rows)-179.8)<.001
assert next(s['start'] for s in rows if s['name']=='pose_proof')==94.5
assert next(s['start'] for s in rows if s['name']=='end')==174.8
records=json.loads((p/'配音生成记录.json').read_text(encoding='utf8'));caps=json.loads((p/'captions.json').read_text(encoding='utf8'))
for a,c in zip(records,records[1:]):assert a['start']+a['duration']+.12<=c['start']
for r in records:
    cap=next(c for c in caps if c['id']==r['id'])
    assert re.sub(r'\s+','',cap['text'])==re.sub(r'\s+','',b.normalize(r['spoken_text']))
ass=(p/'final.ass').read_text(encoding='utf-8-sig')
assert all(s in ass for s in ['PH1A90','RK3576','自制电磁阀转接板','PCIe','1080p','60 FPS','50 ms','99.997%'])
assert not any(s in ass for s in ['提示词','引子','不表示','零漏检','完美筛选'])
moments=[(16.5,'Board models'),(35,'Linked controls'),(48,'Auto annotation'),(88,'Edge evidence'),
         (98,'Pose rejection close-up'),(105,'Selected bowl burst A'),(109,'Selected bowl burst B'),
         (114,'1080p60 / capture-to-HDMI'),(121,'Slow recognition method'),(138,'Category recognition'),(158,'Long category rejection'),(171,'Collected result')]
sheet=Image.new('RGB',(1920,1552),'#0d1d29');d=ImageDraw.Draw(sheet);font=ImageFont.truetype('C:/Windows/Fonts/msyh.ttc',21)
for i,(t,title) in enumerate(moments):
    raw=subprocess.run([str(b.b.FF),'-v','error','-ss',str(t),'-i',str(b.OUT),'-frames:v','1','-vf','scale=640:360','-f','image2pipe','-vcodec','png','-'],capture_output=True,check=True).stdout
    x,y=i%3*640,i//3*388;sheet.paste(Image.open(io.BytesIO(raw)),(x,y+28));d.text((x+7,y+2),f'{t}s {title}',font=font,fill='white')
sheet.save(p/'成片审看.jpg',quality=95)
subprocess.run([str(b.b.FF),'-v','error','-y','-ss','114','-i',str(b.OUT),'-frames:v','1',str(p/'显示参数核对.png')],check=True)
stats=subprocess.run([str(b.b.FF),'-hide_banner','-nostdin','-i',str(b.OUT),'-af','loudnorm=I=-18:TP=-1.5:LRA=9:print_format=json','-vn','-f','null','-'],capture_output=True,text=True,encoding='utf8',check=True).stderr
audio=json.loads(re.findall(r'\{\s*"input_i"[\s\S]*?\}',stats)[-1]);assert float(audio['input_tp'])<-.5
report=dict(output=str(b.OUT),duration_seconds=179.8,export_fps=60,frames=10788,application_seconds=80.3,
            previous_application_seconds=58.3,prefix_seconds=94.5,narration_cues=len(records),subtitles_match_speech=True,
            enlarged_shots=['pose','bowl bursts','HDMI recognition','category action'],
            new_bowl_ranges=[[30,34.9],[40,43]],long_category_action_seconds=1340/60,
            new_frame_by_frame_audit_stopped_on_user_request=True,zero_miss_claim=False,
            display_metrics_basis=plan['metric_basis']['display'],audio_levels=audio,size_bytes=b.OUT.stat().st_size)
(p/'成片核对.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf8')
print(json.dumps(report,ensure_ascii=True),flush=True)
