"""Check the exported edit, speech alignment, highlights and selected frames."""
from pathlib import Path
import io, json, re, subprocess
from PIL import Image, ImageDraw, ImageFont
import build_v15 as b


def main():
    p = Path(__file__).resolve().parent
    info = json.loads(subprocess.run(['ffprobe','-v','error','-show_streams','-show_format','-show_chapters','-of','json',str(b.OUT)],capture_output=True,text=True,encoding='utf8',check=True).stdout)
    v = next(s for s in info['streams'] if s['codec_type']=='video')
    assert (v['width'],v['height'],v['avg_frame_rate'],v['nb_frames'])==(1920,1080,'60/1','10788')
    assert abs(float(info['format']['duration'])-179.8)<.001
    records=json.loads((p/'配音生成记录.json').read_text(encoding='utf8'))
    captions=json.loads((p/'captions.json').read_text(encoding='utf8'))
    assert len(records)==len(captions)==25
    for r in records:
        c=next(c for c in captions if c['id']==r['id'])
        assert c['startMs']/1000<=r['start'] and c['endMs']/1000>=r['start']+r['duration']-.001
    for cue in b.CHANGES:
        r=next(r for r in records if r['id']==cue['id'])
        assert r['spoken_text']==cue['spoken_text'] and r['rate']==0
    console=next(r for r in records if r['id']==25)
    assert console['start']+console['duration']<35.5
    speed=next(r for r in records if r['id']==21)
    assert '每分钟一千二百件' in speed['spoken_text']
    ass=(p/'final.ass').read_text(encoding='utf-8-sig')
    assert not any(word in ass for word in ['引子','提示词','同屏呈现','不表示'])
    for file in ['fewshot_v15_visible_text.json','end_v15_visible_text.json']:
        assert '引子' not in (p/file).read_text(encoding='utf8')
    stats=subprocess.run([str(b.FF),'-hide_banner','-nostdin','-i',str(b.OUT),'-af','loudnorm=I=-18:TP=-1.5:LRA=9:print_format=json','-f','null','-'],capture_output=True,text=True,encoding='utf8',check=True).stderr
    measured=json.loads(re.findall(r'\{\s*"input_i"[\s\S]*?\}',stats)[-1])
    assert float(measured['input_tp'])<-.5
    moments=[(.95,'Opening overview'),(3.3,'Subject title'),(31.6,'Console introduction'),(35.8,'Console follows'),(80.4,'Few-shot deployment'),(144.5,'Sorting action'),(160.3,'Measured speed'),(169.5,'Collected result'),(177,'Final highlights')]
    sheet=Image.new('RGB',(1920,1164),'#0d1d29')
    d=ImageDraw.Draw(sheet);font=ImageFont.truetype('C:/Windows/Fonts/msyh.ttc',21)
    for i,(t,label) in enumerate(moments):
        raw=subprocess.run([str(b.FF),'-v','error','-ss',str(t),'-i',str(b.OUT),'-frames:v','1','-vf','scale=640:360','-f','image2pipe','-vcodec','png','-'],capture_output=True,check=True).stdout
        x,y=i%3*640,i//3*388
        sheet.paste(Image.open(io.BytesIO(raw)),(x,y+28));d.text((x+7,y+2),f'{t:.2f}s {label}',font=font,fill='white')
    sheet.save(p/'成片审看.jpg',quality=95)
    report=dict(output=str(b.OUT),duration_seconds=179.8,video_frames=10788,export_fps=60,
                subtitles_match_narration=True,voice_cues=25,new_speech_rate_percent=0,
                console_introduction_before_page=True,console_introduction_end=console['start']+console['duration'],
                measured_sorting_speed='1200 工件/min',speed_basis='User supplied measured result in this revision',
                highlights=['小样本学习','高速识别','快速部署'],loudness=measured,
                cadence_replacements=json.loads((p/'cadence/replacements.json').read_text(encoding='utf8')),
                size_bytes=b.OUT.stat().st_size)
    (p/'成片核对.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf8')
    print(json.dumps(report,ensure_ascii=True),flush=True)


if __name__=='__main__':
    main()
