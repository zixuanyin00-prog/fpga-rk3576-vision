"""Make room for a closer, longer application demonstration within three minutes."""
from pathlib import Path
import argparse,asyncio,copy,importlib.util,json,re,subprocess,concurrent.futures,sys
from PIL import ImageFont

P=Path(__file__).resolve().parent;M=P.parent;OLD=M/'重剪_v18'
OUT=P/'FPGA视觉系统_实战强化与近景_v19.mp4'
DURATION=179.8


def module(name,path):
    spec=importlib.util.spec_from_file_location(name,path)
    result=importlib.util.module_from_spec(spec);spec.loader.exec_module(result)
    return result


b=module('v19_base',M/'重剪_v15/build_v15.py');b.P=P;b.OUT=OUT
tr=module('v19_transitions',M/'重剪_v12/add_transitions.py');tr.P=P
previous=module('v19_previous',OLD/'build_v18.py')


def plan():return json.loads((P/'edit_plan.json').read_text(encoding='utf8'))


def normalize(text):
    text=previous.normalize(text)
    for before,after in [('H D M I','HDMI'),('一零八零 P','1080p'),('六十帧','60帧'),('五十毫秒','50毫秒')]:
        text=text.replace(before,after)
    return text


def prepare():
    edit=plan();cursor=0;rows=[]
    for item in edit['scenes']:
        s=copy.deepcopy(item);s['start']=round(cursor*60)/60
        s['file']=str(P/'scenes'/(s['name']+'.mp4'))
        rows.append(s);cursor+=s['duration']
    assert abs(cursor-DURATION)<.001,cursor
    (P/'timeline.json').write_text(json.dumps(rows,ensure_ascii=False,indent=2),encoding='utf8')
    chapters=edit['chapters'];metadata=';FFMETADATA1\n'
    for i,c in enumerate(chapters):
        c['end']=chapters[i+1]['start'] if i+1<len(chapters) else DURATION
        metadata+=f'[CHAPTER]\nTIMEBASE=1/1000\nSTART={round(c["start"]*1000)}\nEND={round(c["end"]*1000)}\ntitle={c["title"]}\n'
    (P/'chapters.ffmeta').write_text(metadata,encoding='utf8')
    (P/'章节时间.json').write_text(json.dumps(chapters,ensure_ascii=False,indent=2),encoding='utf8')
    print('Prepared continuous timeline: 179.8s',flush=True)


def render_scene(scene):
    output=Path(scene['file']);output.parent.mkdir(exist_ok=True)
    source=scene['source'];file=Path(source['file']);speed=source.get('playback_speed',1)
    count=round(scene['duration']*60)
    assert source['length']>=scene['duration']*speed-.001
    filters=[f'setpts=(PTS-STARTPTS)/{speed}']
    if source.get('crop'):filters.append(source['crop'])
    filters+=source.get('filters',['scale=1920:1080:flags=lanczos','drawbox=x=0:y=930:w=iw:h=150:color=0x0d1d29@0.65:t=fill'])
    filters+=['setsar=1','fps=60:round=up' if source.get('round_up') else 'fps=60']
    if source.get('endpoint_margin'):filters.append('tpad=stop_mode=clone:stop=2')
    graph=','.join(filters)
    sig=dict(source=source,duration=scene['duration'],size=file.stat().st_size,mtime_ns=file.stat().st_mtime_ns,filter=graph)
    marker=output.with_suffix('.source.json')
    if marker.is_file() and json.loads(marker.read_text(encoding='utf8'))==sig and tr.probe(output,count):
        print('reuse '+scene['name'],flush=True);return
    with output.with_suffix('.log').open('w',encoding='utf8') as log:
        subprocess.run([str(b.FF),'-y','-hide_banner','-nostdin','-ss',str(source.get('start',0)),'-t',str(source['length']+.1),'-i',str(file),
                        '-vf',graph,'-frames:v',str(count),*tr.ENC,str(output)],stdout=log,stderr=log,check=True)
    assert tr.probe(output,count),(scene['name'],count)
    marker.write_text(json.dumps(sig,ensure_ascii=False,indent=2),encoding='utf8')
    print('rendered '+scene['name'],flush=True)


def render():
    rows=json.loads((P/'timeline.json').read_text(encoding='utf8'))
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:list(pool.map(render_scene,rows))


async def voice():
    tts=module('v19_tts',M/'重剪_v6/synthesize_voice.py');tts.P=P;tts.OUT=P/'voice';tts.OUT.mkdir(exist_ok=True)
    for f in tts.OUT.glob('*.mp3'):
        if f.stat().st_size==0:f.unlink()
    old={r['id']:r for r in json.loads((OLD/'配音生成记录.json').read_text(encoding='utf8'))}
    records=[]
    for cue in plan()['voices']:
        if cue.get('reuse_id') is not None:
            result=copy.deepcopy(old[cue['reuse_id']])
            assert result['spoken_text']==cue['spoken_text']
        else:
            tts.RATES[cue['scene']]=0
            result=await tts.generate(dict(cue,end=cue['start']+18))
        assert result['duration']<=cue['end']-cue['start']+.001,(cue['id'],result['duration'],cue['end']-cue['start'])
        result.update(id=cue['id'],scene=cue['scene'],start=cue['start'],end=cue['end'],remaining_silence=round(cue['end']-cue['start']-result['duration'],4))
        records.append(result);print(f'voice {cue["id"]}: {result["duration"]}s',flush=True)
    records.sort(key=lambda r:r['start'])
    for a,c in zip(records,records[1:]):assert a['start']+a['duration']+.12<=c['start'],(a['id'],c['id'])
    (P/'配音生成记录.json').write_text(json.dumps(records,ensure_ascii=False,indent=2),encoding='utf8')


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
        a=max(0,r['start']-.1);c=min(DURATION-.1,r['start']+r['duration']+.15)
        ass+=f'Dialogue: 1,{b.stamp(a)},{b.stamp(c)},Default,,0,0,0,,{{\\fad(100,100)}}'+display.replace('\n',r'\N')+'\n'
        caps.append(dict(id=r['id'],scene=r['scene'],text=display,startMs=round(a*1000),endMs=round(c*1000)))
        srt.append(f'{len(srt)+1}\n{b.stamp(a,True)} --> {b.stamp(c,True)}\n{display}')
        assert re.sub(r'\s+','',display)==re.sub(r'\s+','',normalize(r['spoken_text']))
        checks.append(dict(id=r['id'],spoken_text=r['spoken_text'],subtitle=display,same_words_after_numeric_and_acronym_normalization=True))
    rows=json.loads((P/'timeline.json').read_text(encoding='utf8'));by_name={s['name']:s for s in rows}
    old_rows=json.loads((OLD/'timeline.json').read_text(encoding='utf8'))
    # Map verified pre-application callouts to the actual newly shortened scene.
    for line in old.splitlines():
        if not line.startswith('Dialogue:'):continue
        fields=line.split(',',9)
        if int(fields[0].split(':')[1])<2:continue
        times=[]
        for raw in fields[1:3]:
            h,m,s=map(float,raw.split(':'));times.append(h*3600+m*60+s)
        if times[1]>116.5:continue
        old_scene=next((s for s in old_rows if s['start']<=times[0] and times[1]<=s['start']+s['duration']+.001),None)
        if old_scene is None or old_scene['name'] not in by_name:continue
        scene=by_name[old_scene['name']];speed=scene['source'].get('playback_speed',1);offset=scene['source'].get('start',0)
        local=[t-old_scene['start'] for t in times]
        if scene['source'].get('original_asset_segments'):
            mapped=[]
            for t in local:
                elapsed=0
                for segment in scene['source']['original_asset_segments']:
                    if t<=segment['start']+segment['length']:
                        mapped.append(elapsed+max(0,t-segment['start']));break
                    elapsed+=segment['length']
                else:mapped.append(elapsed)
            local=mapped
        a=scene['start']+(local[0]-offset)/speed
        c=scene['start']+(local[1]-offset)/speed
        a=max(a,scene['start']+.1);c=min(c,scene['start']+scene['duration']-.1)
        if c-a<.4:continue
        fields[1]=b.stamp(a);fields[2]=b.stamp(c);ass+=','.join(fields)+'\n'
    specific={'rk_board':'RK3576｜识别与显示','fpga_board':'安路 PH1A90｜图像采集与预处理','control_board':'自制电磁阀转接板｜脉冲控制'}
    for s in rows:
        label=specific.get(s['name'],s.get('label'))
        if label:ass+=f'Dialogue: 0,{b.stamp(s["start"]+.15)},{b.stamp(s["start"]+s["duration"]-.15)},Rule,,0,0,0,,{{\\fad(120,120)}}{label}\n'
        if s.get('speed_label'):ass+=f'Dialogue: 3,{b.stamp(s["start"]+.3)},{b.stamp(s["start"]+s["duration"]-.3)},Default,,0,0,0,,{{\\an3\\pos(1840,930)\\fs26\\bord1.3\\shad0\\fad(120,120)}}{s["speed_label"]}\n'
    for overlay in edit.get('overlays',[]):
        ass+=f'Dialogue: {overlay.get("layer",3)},{b.stamp(overlay["start"])},{b.stamp(overlay["end"])},Default,,0,0,0,,'+overlay['ass']+'\n'
    assert not any(word in ass for word in ['引子','提示词','不表示','剪辑建议','补素材','零漏检','完美筛选'])
    (P/'final.ass').write_text(ass,encoding='utf-8-sig')
    (P/'captions.json').write_text(json.dumps(caps,ensure_ascii=False,indent=2),encoding='utf8')
    (P/'全片字幕.srt').write_text('\n\n'.join(srt)+'\n',encoding='utf-8-sig')
    (P/'字幕配音逐句核对.json').write_text(json.dumps(checks,ensure_ascii=False,indent=2),encoding='utf8')


def assemble():
    # Execute the shared renderer with its P bound to this edit, preserving all authored cuts.
    old_argv=sys.argv
    sys.argv=['add_transitions.py','--timeline',str(P/'timeline.json')]
    tr.main()
    sys.argv=old_argv
    (P/'video_transitions.mp4').replace(P/'video_clean.mp4')


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('stage',choices=['prepare','render','voice','compose','video','final']);args=parser.parse_args()
    if args.stage=='prepare':prepare()
    elif args.stage=='render':render()
    elif args.stage=='voice':asyncio.run(voice())
    elif args.stage=='compose':captions();b.audio()
    elif args.stage=='video':assemble()
    else:b.final()
