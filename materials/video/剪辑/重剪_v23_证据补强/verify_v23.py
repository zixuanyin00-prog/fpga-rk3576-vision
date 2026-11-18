"""Necessary media and source checks for the requested evidence revision."""
from pathlib import Path
import io,json,re,subprocess
from PIL import Image,ImageDraw,ImageFont
import build_v23 as b

P=Path(__file__).resolve().parent
meta=json.loads(subprocess.run(['ffprobe','-v','error','-show_streams','-show_format','-of','json',str(b.OUT)],capture_output=True,text=True,encoding='utf8',check=True).stdout)
v=next(s for s in meta['streams'] if s['codec_type']=='video');a=next(s for s in meta['streams'] if s['codec_type']=='audio')
assert (v['width'],v['height'],v['avg_frame_rate'],v['nb_frames'])==(1920,1080,'60/1','10788')
assert abs(float(meta['format']['duration'])-179.8)<.001 and a['sample_rate']=='48000' and a['channels']==2
rows=json.loads((P/'timeline.json').read_text('utf8'));by={s['name']:s for s in rows};assert round(sum(s['duration'] for s in rows)*60)==10788
assert by['pose_bowl_lead']['start']==104.5 and by['pose_burst_a']['start']==108.5
assert all(by[n]['source']['start']>=30 for n in ['pose_bowl_lead','pose_burst_a','pose_burst_b'])
slow=by['pose_slow'];assert (slow['start'],slow['duration'],slow['source']['start'],slow['source']['length'],slow['source']['playback_speed'])==(116.5,12.5,25,10,.8)
assert by['pose_inference']['start']==129
segments=by['category_action']['source']['segments'];assert abs(segments[0]['length']-830/60)<.0001 and abs(segments[1]['start']-1070/60)<.0001
assert abs(by['category_action']['start']+segments[0]['length']-164)<.0001
voices=json.loads((P/'配音生成记录.json').read_text('utf8'));caps=json.loads((P/'captions.json').read_text('utf8'))
for x,y in zip(voices,voices[1:]):assert x['start']+x['duration']+.12<=y['start']
for r in voices:
    c=next(c for c in caps if c['id']==r['id']);assert re.sub(r'\s','',c['text'])==re.sub(r'\s','',b.normalize(r['spoken_text']))
ass=(P/'final.ass').read_text('utf-8-sig')
assert all(t in ass for t in ['分拣功能未开启','实验室空压机气流过大','0.8× 慢放','92.87%'])
assert not any(t in ass for t in ['引子','提示词','不表示','零漏检','完美筛选'])
jpeg=json.loads((P/'evidence_isp_compression/software_compression_summary.json').read_text('utf8'));assert jpeg['sample_count']==15 and abs(jpeg['raw_total_bytes']/jpeg['jpeg']['total_bytes']-14.020076613133257)<1e-8
lz=json.loads((P/'evidence_isp_compression/project_lz77_reference_software.json').read_text('utf8'));assert lz['all_yuyv_roundtrip_equal'] and lz['sample_count']==15
tracking=json.loads((P/'evidence_tracking_dimensions/tracking_exact_records.json').read_text('utf8'));assert [r['display_source_frame_id'] for r in tracking]==[11669,11681,11693]
assert all(r['result']['track_id']==998 and r['result']['lifecycle']=='confirmed' for r in tracking)
subprocess.run([str(b.FF),'-v','error','-nostdin','-i',str(b.OUT),'-map','0:v','-map','0:a','-f','null','-'],check=True)
measure=subprocess.run([str(b.FF),'-hide_banner','-nostdin','-i',str(b.OUT),'-vn','-af','loudnorm=I=-18:TP=-1.5:LRA=9:print_format=json','-f','null','-'],capture_output=True,text=True,encoding='utf8',check=True).stderr
levels=json.loads(re.findall(r'\{\s*"input_i"[\s\S]*?\}',measure)[-1]);assert float(levels['input_tp'])<-.5
moments=[(41,'ISP mapping'),(43,'ISP morphology'),(45,'ISP contour'),(72,'Software compression'),(78,'Same logged identity'),(86,'Measured pixel boxes'),(92,'Real transfer receipt'),(105,'Enlarged bowl >30s'),(109,'Enlarged bowl >40s'),(122,'HDMI 25-35s slow'),(130,'Sorting disabled'),(165,'Airflow condition')]
sheet=Image.new('RGB',(1920,1192),'#cbdde9');d=ImageDraw.Draw(sheet);font=ImageFont.truetype('C:/Windows/Fonts/msyh.ttc',19)
for i,(t,label) in enumerate(moments):
    data=subprocess.run([str(b.FF),'-v','error','-ss',str(t),'-i',str(b.OUT),'-frames:v','1','-vf','scale=480:270','-f','image2pipe','-vcodec','png','-'],capture_output=True,check=True).stdout
    x,y=i%4*480,i//4*298;sheet.paste(Image.open(io.BytesIO(data)),(x,y+28));d.text((x+6,y+3),f'{t}s {label}',font=font,fill='#183747')
sheet.crop((0,0,1920,894)).save(P/'成片审看.jpg',quality=96)
for t,name in [(41,'41s_ISP提示已移除'),(122,'2m02_HDMI慢放'),(130,'2m09_未开启分拣说明'),(165,'2m44_气流说明')]:
    subprocess.run([str(b.FF),'-v','error','-y','-ss',str(t),'-i',str(b.OUT),'-frames:v','1',str(P/(name+'.png'))],check=True)
report=dict(output=str(b.OUT),duration=179.8,fps=60,frames=10788,narration_cues=len(voices),subtitles_match_speech=True,decode_passed=True,removed_original_range_seconds=[160,164],front_evidence_extra_seconds=4,slow_source_seconds=[25,35],slow_speed=.8,slow_interpolation='FFmpeg motion compensated, 60fps',jpeg_software_compression_ratio=jpeg['jpeg']['raw_div_encoded'],displayed_compression_rate_percent=92.87,compression_rate_definition='fraction of original bytes saved',lz77_yuyv_roundtrip='15/15',tracking_source_frame_ids=[11669,11681,11693],tracking_identity=998,network_evidence='100 archived receipts, local byte/hash verification',sd_claim='optional supported capability, no saved-success claim',isp_visual_hint_removed=True,audio_levels=levels,size_bytes=b.OUT.stat().st_size)
(P/'成片核对.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),'utf8');print(json.dumps(report,ensure_ascii=True),flush=True)
