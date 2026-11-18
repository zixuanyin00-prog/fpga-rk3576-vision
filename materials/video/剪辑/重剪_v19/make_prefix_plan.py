from pathlib import Path
import json

P=Path(__file__).resolve().parent
OLD=P.parent/'重剪_v18'
rows=json.loads((OLD/'timeline.json').read_text(encoding='utf8'))
lookup={r['name']:r for r in rows}
records=json.loads((OLD/'配音生成记录.json').read_text(encoding='utf8'))
by_id={r['id']:r for r in records}
durations=[('opening_title',2),('intro',4),('chapter_board',1.5),('rk_board',5.5),('fpga_board',5),('control_board',5),
 ('station',7.5),('chapter_console',1.2),('console_image',7.8),('isp_methods',6),('console_annotate',7),
 ('console_train',5.2),('console_run',4.5),('fewshot_value',5.5),('chapter_logic',1.2),('compression_pcie',5.4),
 ('tracking_short',3.2),('trigger_short',3.2),('dimensions',5),('alerts',6.1),('application',2.7)]
scenes=[];start=0
for name,duration in durations:
    old=lookup[name]
    source_start=0.;source_length=float(old['duration'])
    if name=='station':source_start,source_length=1.,9.
    if name=='console_run':source_start,source_length=.4,6.3
    file=old['file']
    if name=='console_annotate':
        # The only waiting interval removed is the blank candidate-generation wait.
        file=str(P/'prefix_assets/console_annotate_trimmed.mp4')
        source_length=8.8
    scenes.append(dict(name=name,start=round(start,4),duration=duration,file=file,source_start=source_start,
        source_length=source_length,playback_speed=round(source_length/duration,10),
        v18_scene_start=old['start'],v18_scene_duration=old['duration'],v18_file=old['file'],
        fps=60,expected_frames=round(duration*60)))
    start+=duration
assert abs(start-94.5)<.00001
timings={1:(.55,7.25),2:(7.9,12.6),3:(13.,17.85),4:(18.3,22.85),5:(23.35,27.4),25:(27.45,31.45),
 6:(31.9,38.9),7:(38.95,45.43),8:(45.7,52.3),9:(52.7,57.5),10:(57.9,62.),24:(62.35,67.6),
 11:(68.55,74.2),12:(74.45,77.35),13:(77.65,80.6),26:(80.9,85.5),27:(85.9,91.65)}
new_words={25:'配套控制台，集成采集、训练与部署。',
 8:'标注页一键生成候选框，再检查位置、指定类别。',
 9:'训练页完成模型训练、转换和现场检查。',
 10:'运行页监控识别结果和设备状态。'}
voices=[]
for identity,(a,b) in timings.items():
    r=by_id[identity]
    v=dict(id=identity,scene=r['scene'],start=a,end=b,spoken_text=new_words.get(identity,r['spoken_text']),rate=0)
    if identity not in new_words:
        v.update(reuse_id=identity,reuse_file=r['file'],reuse_duration=r['duration'])
        assert a+r['duration']<=b+.001,(identity,a+r['duration'],b)
    voices.append(v)
voices.sort(key=lambda v:v['start'])
plan=dict(duration=94.5,scenes=scenes,voices=voices,continuous=True,
    source_timeline=str(OLD/'timeline.json'),source_voice_records=str(OLD/'配音生成记录.json'),
    fewshot_overlaps_removed=True,transition_policy='Frame-exact 0.4s centered blends preserve authored scene starts and total duration',
    caption_padding=dict(lead_seconds=.08,tail_seconds=.10),
    notes=['First 0–23 seconds unchanged.','station keeps the complete pan through the HDMI arrival, from existing clip1–10s at1.2x.',
     'console_annotate_trimmed concatenates existing asset[0.6,2.4] and[4.8,11.8], then plays8.8s at1.257142857x to7s.',
     'ISP voice begins0.55s before its card, over the preceding real edge-processing image; original natural audio retained.',
     'Compression voice begins0.35s before its card, over the technical chapter transition; original natural audio retained.',
     'All voice text matches subtitles; voices remain at natural0% rate.'])
(P/'prefix_plan.json').write_text(json.dumps(plan,ensure_ascii=False,indent=2),encoding='utf8')
print('prefix_plan.json ready:94.5s /21scenes /17voice cues')
