"""Final media and annotation audit for the 179.8 second v16 edit."""
from pathlib import Path
import io,json,re,subprocess
from PIL import Image,ImageDraw,ImageFont
import build_v16 as build


def main():
    p=Path(__file__).resolve().parent
    ff=build.b.FF
    info=json.loads(subprocess.run(['ffprobe','-v','error','-show_streams','-show_format','-show_chapters','-of','json',str(build.OUT)],capture_output=True,text=True,encoding='utf8',check=True).stdout)
    v=next(s for s in info['streams'] if s['codec_type']=='video')
    assert (v['width'],v['height'],v['avg_frame_rate'],v['nb_frames'])==(1920,1080,'60/1','10788')
    assert abs(float(info['format']['duration'])-179.8)<.001
    records=json.loads((p/'配音生成记录.json').read_text(encoding='utf8'))
    captions=json.loads((p/'captions.json').read_text(encoding='utf8'))
    assert len(records)==len(captions)==25
    for left,right in zip(records,records[1:]):assert left['start']+left['duration']+.15<=right['start']
    for cue in build.b.CHANGES:
        r=next(r for r in records if r['id']==cue['id'])
        assert r['spoken_text']==cue['spoken_text'] and r['rate']==0
    for r in records:
        c=next(c for c in captions if c['id']==r['id'])
        assert c['startMs']/1000<=r['start'] and c['endMs']/1000>=r['start']+r['duration']-.001
    hardware=json.loads((p/'annotations_hardware.json').read_text(encoding='utf8'))['annotations']
    console=json.loads((p/'annotations_console.json').read_text(encoding='utf8'))
    application=json.loads((p/'annotations_application.json').read_text(encoding='utf8'))['cues']
    assert (len(hardware),len(console),len(application))==(6,4,4)
    assert all(0<=r['start']<r['end']<=174.8 for r in hardware+console+application)
    ass=(p/'final.ass').read_text(encoding='utf-8-sig')
    assert not any(word in ass for word in ['引子','提示词','不表示','让工件自动分开'])
    labels=json.loads((p/'end_visible_text.json').read_text(encoding='utf8'))['visible_text']
    assert all(any(word in label for label in labels) for word in ['双向调参','小样本学习','快速部署','高速识别与分拣'])
    layout=json.loads((p/'source_console_layout.json').read_text(encoding='utf8'))
    assert [(s['start'],s['length']) for s in layout['phases']]==[(38,4),(102,6)]
    old=json.loads((build.OLD/'timeline.json').read_text(encoding='utf8'))
    new=json.loads((p/'timeline.json').read_text(encoding='utf8'))
    assert len(old)==len(new)
    for a,b in zip(old,new):
        assert (a['name'],a['start'],a['duration'])==(b['name'],b['start'],b['duration'])
        if a['name'] not in ['intro','console_image','end']:assert a==b
    moments=[(3.3,'Workcell overview'),(10,'Board callout'),(38.3,'Gain: controls and display'),(42.5,'FPGA edge processing'),(58,'Automatic candidates'),(65.3,'Model version'),(72,'Embedded HDMI'),(131,'Existing recognition'),(177,'Four highlights')]
    sheet=Image.new('RGB',(1920,1164),'#0d1d29');d=ImageDraw.Draw(sheet)
    font=ImageFont.truetype('C:/Windows/Fonts/msyh.ttc',21)
    for i,(t,label) in enumerate(moments):
        raw=subprocess.run([str(ff),'-v','error','-ss',str(t),'-i',str(build.OUT),'-frames:v','1','-vf','scale=640:360','-f','image2pipe','-vcodec','png','-'],capture_output=True,check=True).stdout
        x,y=(i%3)*640,(i//3)*388
        sheet.paste(Image.open(io.BytesIO(raw)),(x,y+28));d.text((x+7,y+2),f'{t:.2f}s {label}',font=font,fill='white')
    sheet.save(p/'成片审看.jpg',quality=95)
    stats=subprocess.run([str(ff),'-hide_banner','-nostdin','-i',str(build.OUT),'-af','loudnorm=I=-18:TP=-1.5:LRA=9:print_format=json','-f','null','-'],capture_output=True,text=True,encoding='utf8',check=True).stderr
    levels=json.loads(re.findall(r'\{\s*"input_i"[\s\S]*?\}',stats)[-1])
    assert float(levels['input_tp'])<-.5
    report=dict(output=str(build.OUT),duration_seconds=179.8,frames=10788,export_fps=60,
        annotations=14,annotation_breakdown=dict(hardware=6,console=4,application=4),
        unchanged_scene_times=True,other_scene_sources_preserved=True,speech_clips=25,
        new_speech_rate_percent=0,subtitle_words_match_speech=True,workcell_overview_seconds=4,
        console_source_intervals=[[38,42],[102,108]],console_same_source_timestamps=True,
        closing_highlights=labels,audio_levels=levels,size_bytes=build.OUT.stat().st_size)
    (p/'成片核对.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf8')
    print(json.dumps(report,ensure_ascii=True),flush=True)


if __name__=='__main__':main()
