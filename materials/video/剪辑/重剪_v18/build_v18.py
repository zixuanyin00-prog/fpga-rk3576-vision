"""Re-edit verified footage into pose and category demonstrations."""
from pathlib import Path
import argparse, asyncio, copy, importlib.util, json, re, subprocess
from PIL import ImageFont

P=Path(__file__).resolve().parent
M=P.parent
OLD=M/'重剪_v17'
OUT=P/'FPGA视觉系统_高速姿态与类别检测_v18.mp4'
DURATION=179.8


def module(name,path):
    spec=importlib.util.spec_from_file_location(name,path)
    result=importlib.util.module_from_spec(spec);spec.loader.exec_module(result)
    return result


b=module('v18_base',M/'重剪_v15/build_v15.py');b.P=P;b.OLD=OLD;b.OUT=OUT
tr=module('v18_transitions',M/'重剪_v12/add_transitions.py');tr.P=P


def plan():
    return json.loads((P/'edit_plan.json').read_text(encoding='utf8'))


def prepare():
    edit=plan()
    rows=copy.deepcopy(json.loads((OLD/'timeline.json').read_text(encoding='utf8')))
    rows=[s for s in rows if s['start']<116.5]
    cursor=116.5
    for item in edit['scenes']:
        s=copy.deepcopy(item);s['start']=round(cursor,4)
        s['file']=str(P/(s['name']+'.mp4'))
        rows.append(s);cursor+=s['duration']
    last=copy.deepcopy(next(s for s in json.loads((OLD/'timeline.json').read_text(encoding='utf8')) if s['name']=='end'))
    assert abs(cursor-last['start'])<.001,(cursor,last['start'])
    rows.append(last)
    assert abs(sum(s['duration'] for s in rows)-1-DURATION)<.001
    (P/'timeline.json').write_text(json.dumps(rows,ensure_ascii=False,indent=2),encoding='utf8')
    changes={r['id'] for r in edit['voices']}
    records=json.loads((OLD/'配音生成记录.json').read_text(encoding='utf8'))
    kept=[r for r in records if r['id'] not in changes and (r['start']<113.5 or r['id']==23)]
    (P/'配音复用记录.json').write_text(json.dumps(kept,ensure_ascii=False,indent=2),encoding='utf8')
    chapters=json.loads((OLD/'章节时间.json').read_text(encoding='utf8'))
    chapters[-1]['title']='04 实战：姿态检测与类别检测'
    chapters.append(dict(title='姿态检测与高速运行',start=116.5,end=edit['category_start']))
    chapters.append(dict(title='类别检测与自动分拣',start=edit['category_start'],end=174.8))
    chapters.append(dict(title='系统亮点',start=174.8,end=DURATION))
    # Chapter metadata is sequential rather than overlapping.
    chapters[-4]['end']=116.5
    metadata=';FFMETADATA1\n'
    for c in chapters:
        metadata+=f'[CHAPTER]\nTIMEBASE=1/1000\nSTART={round(c["start"]*1000)}\nEND={round(c["end"]*1000)}\ntitle={c["title"]}\n'
    (P/'chapters.ffmeta').write_text(metadata,encoding='utf8')
    (P/'章节时间.json').write_text(json.dumps(chapters,ensure_ascii=False,indent=2),encoding='utf8')
    print('Prepared 179.8s pose/category edit',flush=True)


async def voice():
    tts=module('v18_tts',M/'重剪_v6/synthesize_voice.py');tts.P=P;tts.OUT=P/'voice';tts.OUT.mkdir(exist_ok=True)
    for f in tts.OUT.glob('*.mp3'):
        if f.stat().st_size==0:f.unlink()
    records=json.loads((P/'配音复用记录.json').read_text(encoding='utf8'))
    for cue in plan()['voices']:
        tts.RATES[cue['scene']]=0
        result=await tts.generate(dict(cue,end=cue['start']+18))
        assert result['rate']==0
        assert result['duration']<=cue['end']-cue['start']+.001,(cue['id'],result['duration'],cue['end']-cue['start'])
        result.update(start=cue['start'],end=cue['end'],remaining_silence=round(cue['end']-cue['start']-result['duration'],4))
        records.append(result)
        print(f'voice {cue["id"]}: {result["duration"]}s',flush=True)
    records.sort(key=lambda r:r['start'])
    for a,c in zip(records,records[1:]):assert a['start']+a['duration']+.15<=c['start'],(a['id'],c['id'])
    (P/'配音生成记录.json').write_text(json.dumps(records,ensure_ascii=False,indent=2),encoding='utf8')


def normalize(text):
    for before,after in [('F P G A','FPGA'),('R K','RK'),('P C I E','PCIe'),('S D','SD'),
                         ('P H 一 A 九零','PH1A90'),('三五七六','3576'),('四七三','473'),
                         ('一零零四','1004'),('一千二百','1200'),('百分之九十九点九九七','99.997%')]:
        text=text.replace(before,after)
    return text.replace('RK 3576','RK3576')


def captions():
    edit=plan();old=(OLD/'final.ass').read_text(encoding='utf-8-sig')
    ass='\n'.join(line for line in old.splitlines() if not line.startswith('Dialogue:'))+'\n'
    caps=[];srt=[];checks=[];font=ImageFont.truetype('C:/Windows/Fonts/msyh.ttc',44)
    for r in json.loads((P/'配音生成记录.json').read_text(encoding='utf8')):
        words=normalize(r['spoken_text']);display=words
        if font.getlength(words)>1320:
            cuts=[i+1 for i,c in enumerate(words) if c in '，；：。、' and 4<i<len(words)-4]
            cut=min(cuts,key=lambda i:max(font.getlength(words[:i]),font.getlength(words[i:]))) if cuts else len(words)//2
            display=words[:cut]+'\n'+words[cut:]
        a=max(0,r['start']-.12);c=min(DURATION-.1,r['start']+r['duration']+.18)
        ass+=f'Dialogue: 1,{b.stamp(a)},{b.stamp(c)},Default,,0,0,0,,{{\\fad(120,120)}}'+display.replace('\n',r'\N')+'\n'
        caps.append(dict(id=r['id'],scene=r['scene'],text=display,startMs=round(a*1000),endMs=round(c*1000)))
        srt.append(f'{len(srt)+1}\n{b.stamp(a,True)} --> {b.stamp(c,True)}\n{display}')
        assert re.sub(r'\s+','',display)==re.sub(r'\s+','',normalize(r['spoken_text']))
        checks.append(dict(id=r['id'],spoken_text=r['spoken_text'],subtitle=display,same_words_after_numeric_and_acronym_normalization=True))
    # Retain verified source-specific callouts preceding the new demonstration.
    for line in old.splitlines():
        if not line.startswith('Dialogue:'):continue
        fields=line.split(',',9)
        h,m,s=map(float,fields[2].split(':'));end=h*3600+m*60+s
        if int(fields[0].split(':')[1])>=2 and end<=113.5:ass+=line+'\n'
    rows=json.loads((P/'timeline.json').read_text(encoding='utf8'))
    specific={'rk_board':'RK3576｜识别与显示','fpga_board':'安路 PH1A90｜图像采集与预处理',
              'control_board':'自制电磁阀转接板｜脉冲控制'}
    for s in rows:
        label=specific.get(s['name'],s.get('label'))
        if label:
            ass+=f'Dialogue: 0,{b.stamp(s["start"]+.15)},{b.stamp(s["start"]+s["duration"]-.15)},Rule,,0,0,0,,{{\\fad(120,120)}}{label}\n'
        if s.get('speed_label'):
            ass+=f'Dialogue: 3,{b.stamp(s["start"]+.3)},{b.stamp(s["start"]+s["duration"]-.3)},Default,,0,0,0,,{{\\an3\\pos(1840,930)\\fs26\\bord1.3\\shad0\\fad(120,120)}}{s["speed_label"]}\n'
    for overlay in edit.get('overlays',[]):
        ass+=f'Dialogue: {overlay.get("layer",3)},{b.stamp(overlay["start"])},{b.stamp(overlay["end"])},Default,,0,0,0,,'+overlay['ass']+'\n'
    adapter=P/'annotations_adapter.ass'
    if adapter.is_file():
        ass+='\n'.join(line for line in adapter.read_text(encoding='utf-8-sig').splitlines() if line.startswith('Dialogue:'))+'\n'
    assert not any(word in ass for word in ['引子','提示词','不表示','剪辑建议','补素材'])
    (P/'final.ass').write_text(ass,encoding='utf-8-sig')
    (P/'captions.json').write_text(json.dumps(caps,ensure_ascii=False,indent=2),encoding='utf8')
    (P/'全片字幕.srt').write_text('\n\n'.join(srt)+'\n',encoding='utf-8-sig')
    (P/'字幕配音逐句核对.json').write_text(json.dumps(checks,ensure_ascii=False,indent=2),encoding='utf8')


def video():
    rows=json.loads((P/'timeline.json').read_text(encoding='utf8'))
    previous=json.loads((OLD/'timeline.json').read_text(encoding='utf8'))
    application=next(s for s in previous if s['name']=='application')
    parts=P/'transition_parts';parts.mkdir(exist_ok=True)
    def render(name,inputs,graph,frames):
        file=parts/name;args=[]
        for source in inputs:args+=['-i',str(source)]
        signature=dict(sources=[dict(file=str(src),size=src.stat().st_size,mtime_ns=src.stat().st_mtime_ns) for src in inputs],graph=graph,frames=frames)
        tr.render(file,args,graph,frames,signature,parts)
        return file
    sources=[Path(line[6:-1]) for line in (OLD/'transition_parts/concat.txt').read_text(encoding='utf8').splitlines()]
    stop=next(i for i,f in enumerate(sources) if f.name=='bridge_new_06.mp4')
    order=sources[:stop]
    post=[s for s in rows if s['start']>=116.5]
    order.append(render('bridge_into_pose.mp4',[Path(application['file']),Path(post[0]['file'])],tr.bridge_graph(180),24))
    for i,s in enumerate(post):
        file=Path(s['file']);frames=round(s['duration']*60);a=12;c=frames-(12 if i<len(post)-1 else 0)
        assert tr.probe(file,frames),(s['name'],file)
        order.append(render(f'body_app_{i:02}.mp4',[file],f'[0:v]trim=start_frame={a}:end_frame={c},setpts=PTS-STARTPTS,setsar=1,fps=60[v]',c-a))
        if i<len(post)-1:order.append(render(f'bridge_app_{i:02}.mp4',[file,Path(post[i+1]['file'])],tr.bridge_graph(frames),24))
    concat=parts/'concat.txt';concat.write_text(''.join("file '"+f.as_posix()+"'\n" for f in order),encoding='utf8')
    with (parts/'assemble.log').open('w',encoding='utf8') as log:
        subprocess.run([str(b.FF),'-y','-hide_banner','-nostdin','-f','concat','-safe','0','-i',str(concat),'-map','0:v:0','-c:v','copy','-an','-movflags','+faststart',str(P/'video_clean.mp4')],stdout=log,stderr=log,check=True)
    assert tr.probe(P/'video_clean.mp4',10788)
    print('Assembled 179.8s / 10788 frames',flush=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('stage',choices=['prepare','voice','compose','video','final']);args=parser.parse_args()
    if args.stage=='prepare':prepare()
    elif args.stage=='voice':asyncio.run(voice())
    elif args.stage=='compose':captions();b.audio()
    elif args.stage=='video':video()
    else:b.final()
