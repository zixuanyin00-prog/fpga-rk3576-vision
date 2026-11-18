from pathlib import Path
import copy,io,json,shutil,subprocess,wave
import numpy as np
from PIL import Image,ImageDraw,ImageFont
import build_v13 as b

P=Path(__file__).resolve().parent
old=json.loads((b.OLD/'timeline.json').read_text(encoding='utf8'))
new=json.loads((P/'timeline.json').read_text(encoding='utf8'))
original_rows=[s for s in new if s['name'] not in ['opening_title','fewshot_value']]
assert len(old)==len(original_rows)==24
for a,c in zip(old,original_rows):
    expected=copy.deepcopy(a);expected['start']=b.shift(a['start'])
    assert c==expected
assert new[0]['name']=='opening_title' and new[0]['duration']==2
addition=next(s for s in new if s['name']=='fewshot_value')
assert addition['duration']==7 and addition['start']==77
assert b.tr.probe(P/'opening_title.mp4',120) and b.tr.probe(P/'fewshot_value.mp4',420)
assert round(sum(s['duration'] for s in new)-1,4)==179.8
meta=json.loads(subprocess.run([shutil.which('ffprobe'),'-v','error','-show_streams','-show_format','-show_chapters','-of','json',str(b.OUT)],capture_output=True,text=True,encoding='utf8',check=True).stdout)
v=next(s for s in meta['streams'] if s['codec_type']=='video');a=next(s for s in meta['streams'] if s['codec_type']=='audio')
assert (v['width'],v['height'],v['avg_frame_rate'],v['nb_frames'])==(1920,1080,'60/1','10788')
assert abs(float(v['duration'])-179.8)<.001
assert 179<=float(meta['format']['duration'])<=180
assert a['sample_rate']=='48000' and a['channels']==2
chapters=json.loads((P/'章节时间.json').read_text(encoding='utf8'))
assert len(meta['chapters'])==len(chapters)==5
for expected,actual in zip(chapters,meta['chapters']):
    assert actual['tags']['title']==expected['title']
    assert abs(float(actual['start_time'])-expected['start'])<.002
    assert abs(float(actual['end_time'])-expected['end'])<.002
old_caps=json.loads((b.OLD/'captions.json').read_text(encoding='utf8'))
caps=json.loads((P/'captions.json').read_text(encoding='utf8'));by_id={c['id']:c for c in caps}
for c in old_caps:
    expected=dict(c);expected.update(startMs=round(b.shift(c['startMs']/1000)*1000),endMs=round(b.shift(c['endMs']/1000)*1000))
    assert by_id[c['id']]==expected
records=json.loads((P/'配音生成记录.json').read_text(encoding='utf8'));by_record={r['id']:r for r in records}
old_records=json.loads((b.OLD/'配音生成记录.json').read_text(encoding='utf8'))
for r in old_records:
    expected=dict(r);expected.update(start=b.shift(r['start']),end=b.shift(r['end']))
    assert by_record[r['id']]==expected
assert len(records)==len(caps)==24
last=0
for r in records:
    assert r['start']>=last-.001
    assert r['start']+r['duration']<=r['end']+.025
    c=by_id[r['id']]
    assert b.builder.normalize(c['text'])==b.builder.normalize(r['spoken_text'])
    assert c['startMs']/1000<=r['start']+.001 and c['endMs']/1000>=r['start']+r['duration']-.001
    last=r['start']+r['duration']
def read(file):
    with wave.open(str(file),'rb') as f:
        assert f.getframerate()==48000 and f.getnchannels()==2 and f.getsampwidth()==2
        return np.frombuffer(f.readframes(f.getnframes()),dtype='<i2').reshape(-1,2)
baseline_audio=read(b.OLD/'混音.wav');output_audio=read(P/'混音.wav')
assert len(output_audio)==round(179.8*48000)
for r in old_records:
    x=round(r['start']*48000);n=round(r['duration']*48000);y=round(b.shift(r['start'])*48000)
    assert np.array_equal(output_audio[y:y+n],baseline_audio[x:x+n])
font=ImageFont.truetype('C:/Windows/Fonts/msyh.ttc',21)
times=[(1,'Restored title'),(2.8,'Original introduction'),(76.6,'Unchanged console'),(77.3,'New dissolve in'),(79.5,'New value segment'),(82.5,'New complete state'),(83.75,'New dissolve out'),(85.5,'Original chapter continues'),(179,'Original ending')]
sheet=Image.new('RGB',(1920,1164),'#0d1d29');d=ImageDraw.Draw(sheet)
for i,(t,label) in enumerate(times):
    raw=subprocess.run([str(b.FF),'-v','error','-ss',str(t),'-i',str(b.OUT),'-frames:v','1','-vf','scale=640:360','-f','image2pipe','-vcodec','png','-'],capture_output=True,check=True).stdout
    x,y=(i%3)*640,(i//3)*388;sheet.paste(Image.open(io.BytesIO(raw)),(x,y+28));d.text((x+7,y+2),f'{t:.2f}s {label}',font=font,fill='white')
sheet.save(P/'新增两处审看.jpg',quality=95)
report=dict(output=str(b.OUT),duration_seconds=179.8,format_duration=float(meta['format']['duration']),within_2_59_to_3_00=True,video_frames=10788,export_fps=60,original_scenes_preserved=24,original_scene_files_durations_sources_unchanged=True,original_voice_cues_preserved=23,original_speech_pcm_intervals_identical=True,original_subtitle_words_unchanged=True,restored_title_seconds=2,new_animation_seconds=7,new_dissolves_seconds=[.5,.5],new_narration_rate=by_record[24]['rate'],new_subtitles_match_speech=True,chapters=5,size_bytes=b.OUT.stat().st_size)
(P/'成片核对.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf8')
print(json.dumps(report,ensure_ascii=True),flush=True)
