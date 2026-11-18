"""Necessary media checks for the two requested local changes."""
from pathlib import Path
import hashlib,io,json,re,subprocess,wave
import numpy as np
from PIL import Image,ImageDraw,ImageFont
import build_v25 as m

P=m.P;OLD=m.OLD;b=m.b
rows=json.loads((P/'timeline.json').read_text('utf8'));oldrows=json.loads((OLD/'timeline.json').read_text('utf8'))
assert [a['name'] for a,z in zip(rows,oldrows) if a!=z]==['pose_proof']
assert all((a['start'],a['duration'])==(z['start'],z['duration']) for a,z in zip(rows,oldrows))
s=next(x for x in rows if x['name']=='pose_proof');assert (s['start'],s['duration'],s['source']['start'],s['source']['length'],s['source']['playback_speed'])==(103,6,.3,9,1.5)
assert s['speed_label']=='1.5×' and b.tr.probe(Path(s['file']),360)
voices=json.loads((P/'配音生成记录.json').read_text('utf8'));oldvoices=json.loads((OLD/'配音生成记录.json').read_text('utf8'))
assert [a['id'] for a,z in zip(voices,oldvoices) if a!=z]==[30]
caps=json.loads((P/'captions.json').read_text('utf8'));by={x['id']:x for x in caps}
assert next(x['spoken_text'] for x in voices if x['id']==30)==by[30]['text']=='慢放可见，白色电阻被准确吹出。'
assert all(a['start']+a['duration']+.12<=z['start'] for a,z in zip(voices,voices[1:]))
assert all(a['endMs']<=z['startMs'] for a,z in zip(caps,caps[1:]))
for v in voices:
    assert re.sub(r'\s','',by[v['id']]['text'])==re.sub(r'\s','',b.normalize(v['spoken_text']))
    assert v['duration']<=v['end']-v['start']+.001
ass=(P/'final.ass').read_text('utf-8-sig');assert '两段慢放' not in ass and '慢放可见，白色电阻被准确吹出。' in ass
assert all(x not in ass for x in ['提示词','引子','不表示','零漏检','完美筛选'])
meta=json.loads(subprocess.run(['ffprobe','-v','error','-show_streams','-show_format','-of','json',str(b.OUT)],capture_output=True,text=True,encoding='utf8',check=True).stdout)
v=next(s for s in meta['streams'] if s['codec_type']=='video');a=next(s for s in meta['streams'] if s['codec_type']=='audio')
assert (v['width'],v['height'],v['avg_frame_rate'],v['nb_frames'])==(1920,1080,'60/1','10788')
assert abs(float(meta['format']['duration'])-179.8)<.002 and a['sample_rate']=='48000' and a['channels']==2
subprocess.run([str(b.FF),'-v','error','-nostdin','-i',str(b.OUT),'-map','0:v','-map','0:a','-f','null','-'],check=True)
with wave.open(str(P/'混音.wav'),'rb') as w:audio=np.frombuffer(w.readframes(w.getnframes()),dtype='<i2').astype(float)/32768
peak=20*np.log10(np.max(np.abs(audio)));assert peak<=-1.7
sheet=Image.new('RGB',(1920,1136),'#cbdde9');d=ImageDraw.Draw(sheet);font=ImageFont.truetype('C:/Windows/Fonts/msyh.ttc',24)
for i,t in enumerate([104,106,108,110]):
    data=subprocess.run([str(b.FF),'-v','error','-ss',str(t),'-i',str(b.OUT),'-frames:v','1','-vf','scale=960:540','-f','image2pipe','-vcodec','png','-'],capture_output=True,check=True).stdout
    x,y=i%2*960,i//2*568;sheet.paste(Image.open(io.BytesIO(data)),(x,y+28));d.text((x+6,y+1),str(t)+'s',font=font,fill='#183747')
sheet.save(P/'两处修改审看.jpg',quality=96)
sha=hashlib.sha256()
with b.OUT.open('rb') as f:
    for chunk in iter(lambda:f.read(8*1024*1024),b''):sha.update(chunk)
report=dict(output=str(b.OUT),duration=179.8,fps=60,frames=10788,changed_scene='pose_proof',source_seconds=[.3,9.3],speed=1.5,stabilization=s['source']['stabilization'],changed_voice_id=30,changed_sentence=by[30]['text'],other_scenes_identical=True,other_voice_records_identical=True,all_cut_times_identical=True,subtitles_match_speech=True,no_subtitle_overlap=True,decode_passed=True,mix_sample_peak_dbfs=round(float(peak),2),sha256=sha.hexdigest(),size_bytes=b.OUT.stat().st_size)
(P/'成片核对.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),'utf8');print(json.dumps(report,ensure_ascii=False),flush=True)
