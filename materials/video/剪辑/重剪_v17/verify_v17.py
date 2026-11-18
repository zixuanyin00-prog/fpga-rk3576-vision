"""Verify the re-timed 179.8s film, source scopes, speech and representative frames."""
from pathlib import Path
import io,json,re,subprocess
from PIL import Image,ImageDraw,ImageFont
import build_v17 as build


def main():
    p=Path(__file__).resolve().parent;ff=build.b.FF
    data=json.loads(subprocess.run(['ffprobe','-v','error','-show_streams','-show_format','-show_chapters','-of','json',str(build.OUT)],capture_output=True,text=True,encoding='utf8',check=True).stdout)
    v=next(s for s in data['streams'] if s['codec_type']=='video')
    assert (v['width'],v['height'],v['avg_frame_rate'],v['nb_frames'])==(1920,1080,'60/1','10788')
    assert abs(float(data['format']['duration'])-179.8)<.001
    rows=json.loads((p/'timeline.json').read_text(encoding='utf8'))
    by_name={r['name']:r for r in rows}
    assert abs(sum(r['duration'] for r in rows)-1-179.8)<.001
    assert by_name['material_prepare']['start']==127 and by_name['material_prepare']['duration']==6
    assert by_name['app_execute']['start']==146.8 and by_name['app_execute']['duration']==19.5
    assert by_name['app_collect']['start']==166.3 and by_name['end']['start']==174.8
    assert len(data['chapters'])==5
    expected=json.loads((p/'章节时间.json').read_text(encoding='utf8'))
    for a,c in zip(expected,data['chapters']):
        assert a['title']==c['tags']['title'] and abs(a['start']-float(c['start_time']))<.001
    records=json.loads((p/'配音生成记录.json').read_text(encoding='utf8'))
    caps=json.loads((p/'captions.json').read_text(encoding='utf8'))
    assert len(records)==len(caps)==26
    for a,c in zip(records,records[1:]):assert a['start']+a['duration']+.15<=c['start']
    for cue in build.CHANGES:
        r=next(r for r in records if r['id']==cue['id'])
        assert r['spoken_text']==cue['spoken_text'] and r['rate']==0
    for r in records:
        c=next(c for c in caps if c['id']==r['id'])
        assert c['startMs']/1000<=r['start']+.001 and c['endMs']/1000>=r['start']+r['duration']-.001
    ass=(p/'final.ass').read_text(encoding='utf-8-sig')
    assert '99.997%' in ass and '各组测试召回率' in ass and round((1-3/100000)*100,3)==99.997
    assert not any(word in ass for word in ['引子','提示词','不表示','毫米','mm精度'])
    visible='\n'.join((p/file).read_text(encoding='utf8') for file in ['isp_methods_visible_text.json','compression_pcie_visible_text.json','dimensions_visible_text.json','alerts_visible_text.json','end_visible_text.json'])
    assert all(word in visible for word in ['JPEG','LZ77','PCIe','Sobel','白平衡','px','SFTP','SD','99.997%'])
    assert not any(word in visible for word in ['Canny','CLAHE','HDR'])
    assert '可选' in visible
    material=json.loads((p/'source_material.json').read_text(encoding='utf8'))
    assert material['playback_speed']==1 and {r['model'] for r in material['segments']}=={'473','1004'}
    moments=[(48,'ISP methods'),(88,'Compression and PCIe'),(93.5,'Cross-frame association'),(97.8,'Crossing pulse'),(102.5,'Pixel dimensions'),(110,'Edge evidence'),(128.5,'473 materials'),(131.5,'1004 materials'),(135.4,'Parallel detection and recall'),(154,'Classification action'),(169.5,'Collected result'),(177,'Combined highlights')]
    sheet=Image.new('RGB',(1920,1552),'#0d1d29');d=ImageDraw.Draw(sheet);font=ImageFont.truetype('C:/Windows/Fonts/msyh.ttc',21)
    for i,(t,label) in enumerate(moments):
        raw=subprocess.run([str(ff),'-v','error','-ss',str(t),'-i',str(build.OUT),'-frames:v','1','-vf','scale=640:360','-f','image2pipe','-vcodec','png','-'],capture_output=True,check=True).stdout
        x,y=i%3*640,i//3*388
        sheet.paste(Image.open(io.BytesIO(raw)),(x,y+28));d.text((x+7,y+2),f'{t:.2f}s {label}',font=font,fill='white')
    sheet.save(p/'成片审看.jpg',quality=95)
    subprocess.run([str(ff),'-v','error','-y','-ss','135.4','-i',str(build.OUT),'-frames:v','1',str(p/'召回率实拍核对.png')],check=True)
    stats=subprocess.run([str(ff),'-hide_banner','-nostdin','-i',str(build.OUT),'-af','loudnorm=I=-18:TP=-1.5:LRA=9:print_format=json','-f','null','-'],capture_output=True,text=True,encoding='utf8',check=True).stderr
    audio=json.loads(re.findall(r'\{\s*"input_i"[\s\S]*?\}',stats)[-1]);assert float(audio['input_tp'])<-.5
    report=dict(output=str(build.OUT),duration_seconds=179.8,frames=10788,export_fps=60,
        narration_cues=26,subtitles_match_speech=True,new_narration_rate_percent=0,
        isp_methods=['demosaic','white_balance','gaussian','histogram_mapping','foreground_segmentation','morphology','sobel','color_edge_overlay'],
        compression_scope='Board-verified optional preview/archive implementation; native RGB inference remains separate',
        industrial_dimensions='Per-target native-coordinate bounding-box pixel width/height',
        anomaly_scope='Terminal unconfirmed/identity-lost evidence, SFTP recovery and configurable mounted SD storage',
        recall_percent=99.997,recall_basis='User confirmed 1 - 3/100000 across test groups in this revision',
        sorting_speed='1200 workpieces/min, user supplied measured result',
        whole_material_and_sorting_speed_unchanged=True,audio_levels=audio,size_bytes=build.OUT.stat().st_size)
    (p/'成片核对.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf8')
    print(json.dumps(report,ensure_ascii=True),flush=True)


if __name__=='__main__':main()
