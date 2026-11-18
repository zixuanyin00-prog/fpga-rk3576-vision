"""Check delivered media, new source usage, pacing, captions and visual samples."""
from pathlib import Path
import io,json,re,subprocess
from PIL import Image,ImageDraw,ImageFont
import build_v18 as build


def main():
    p=Path(__file__).resolve().parent;ff=build.b.FF
    data=json.loads(subprocess.run(['ffprobe','-v','error','-show_streams','-show_format','-show_chapters','-of','json',str(build.OUT)],capture_output=True,text=True,encoding='utf8',check=True).stdout)
    v=next(s for s in data['streams'] if s['codec_type']=='video')
    assert (v['width'],v['height'],v['avg_frame_rate'],v['nb_frames'])==(1920,1080,'60/1','10788')
    assert abs(float(data['format']['duration'])-179.8)<.001
    rows=json.loads((p/'timeline.json').read_text(encoding='utf8'))
    by_name={r['name']:r for r in rows}
    assert abs(sum(r['duration'] for r in rows)-1-179.8)<.001
    edit=build.plan()
    for name in ['pose_fast','pose_slow','pose_inference','category_monitor']:
        source=by_name[name]['source'];assert Path(source['file']).is_file()
    assert by_name['pose_fast']['duration']>=15 and by_name['pose_fast']['source']['playback_speed']==1.3
    assert by_name['pose_slow']['source']['playback_speed']==.5
    assert '高速整体演示（振动盘）' in by_name['pose_fast']['source']['file']
    assert '高速整体演示（HDMI）' in by_name['pose_slow']['source']['file']
    assert '最高速推理展示（无电磁阀）' in by_name['pose_inference']['source']['file']
    assert Path(by_name['category_monitor']['source']['file']).name=='视频.mp4'
    assert by_name['pose_fast']['source']['start']+by_name['pose_fast']['source']['length']<=59
    assert by_name['category_material']['start']==edit['category_start']
    expected=json.loads((p/'章节时间.json').read_text(encoding='utf8'))
    assert len(data['chapters'])==len(expected)
    for a,c in zip(expected,data['chapters']):
        assert a['title']==c['tags']['title'] and abs(a['start']-float(c['start_time']))<.001
    records=json.loads((p/'配音生成记录.json').read_text(encoding='utf8'))
    caps=json.loads((p/'captions.json').read_text(encoding='utf8'))
    assert len(records)==len(caps)
    for a,c in zip(records,records[1:]):assert a['start']+a['duration']+.15<=c['start']
    for r in records:
        c=next(c for c in caps if c['id']==r['id'])
        assert re.sub(r'\s+','',c['text'])==re.sub(r'\s+','',build.normalize(r['spoken_text']))
        assert c['startMs']/1000<=r['start']+.001 and c['endMs']/1000>=r['start']+r['duration']-.001
    for cue in edit['voices']:
        r=next(r for r in records if r['id']==cue['id']);assert r['spoken_text']==cue['spoken_text'] and r['rate']==0
    ass=(p/'final.ass').read_text(encoding='utf-8-sig')
    assert all(s in ass for s in ['PH1A90','RK3576','PCIe','转接卡','自制电磁阀转接板','1.3×','0.5×','99.997%','473','1004'])
    assert not any(s in ass for s in ['引子','提示词','不表示','剪辑建议','补素材'])
    moments=[(10,'RK3576'),(16.5,'PH1A90 / PCIe adapter'),(20,'Custom valve adapter'),(48,'ISP'),
             (124,'Continuous pose sorting'),(139.2,'Slow real detection'),(144.5,'Independent recognition'),
             (149,'Category materials'),(155.5,'Category detection'),(162,'Valve sorting'),(171,'Collected result'),(177,'Highlights')]
    sheet=Image.new('RGB',(1920,1552),'#0d1d29');d=ImageDraw.Draw(sheet);font=ImageFont.truetype('C:/Windows/Fonts/msyh.ttc',21)
    for i,(t,label) in enumerate(moments):
        raw=subprocess.run([str(ff),'-v','error','-ss',str(t),'-i',str(build.OUT),'-frames:v','1','-vf','scale=640:360','-f','image2pipe','-vcodec','png','-'],capture_output=True,check=True).stdout
        x,y=i%3*640,i//3*388;sheet.paste(Image.open(io.BytesIO(raw)),(x,y+28));d.text((x+7,y+2),f'{t:.2f}s {label}',font=font,fill='white')
    sheet.save(p/'成片审看.jpg',quality=95)
    for t,name in [(16.5,'板卡标注核对'),(139.2,'慢放方法核对'),(162,'筛选效果核对')]:
        subprocess.run([str(ff),'-v','error','-y','-ss',str(t),'-i',str(build.OUT),'-frames:v','1',str(p/(name+'.png'))],check=True)
    stats=subprocess.run([str(ff),'-hide_banner','-nostdin','-i',str(build.OUT),'-af','loudnorm=I=-18:TP=-1.5:LRA=9:print_format=json','-f','null','-'],capture_output=True,text=True,encoding='utf8',check=True).stderr
    audio=json.loads(re.findall(r'\{\s*"input_i"[\s\S]*?\}',stats)[-1]);assert float(audio['input_tp'])<-.5
    decoded=subprocess.run([str(ff),'-v','error','-nostdin','-i',str(build.OUT),'-map','0:v:0','-f','null','-'],capture_output=True,text=True,encoding='utf8',check=True)
    assert not decoded.stderr.strip(),decoded.stderr
    report=dict(output=str(build.OUT),duration_seconds=179.8,frames=10788,export_fps=60,
                narration_cues=len(records),subtitles_match_speech=True,new_narration_rate_percent=0,
                requested_footage_used=True,continuous_high_speed_pose_seconds=by_name['pose_fast']['duration'],
                slow_motion_source='120 FPS HDMI camera footage, 0.5x',
                category_rule='reject 473, retain 1004',highest_speed_clip='independent pose recognition only',
                model_or_board_operations=False,recall_basis='User confirmed all test groups, 99.997%',
                sorting_speed_basis='User supplied measured 1200 workpieces/min; not derived from 1.3x footage',
                audio_levels=audio,decode_errors=0,size_bytes=build.OUT.stat().st_size)
    (p/'成片核对.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf8')
    print(json.dumps(report,ensure_ascii=True),flush=True)


if __name__=='__main__':main()
