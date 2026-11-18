from pathlib import Path
import argparse, asyncio, copy, importlib.util, json, re, subprocess, wave
import numpy as np
from PIL import ImageFont

P=Path(__file__).resolve().parent
M=P.parent
OLD=M/'重剪_v7'

def module(name,path):
    spec=importlib.util.spec_from_file_location(name,path)
    mod=importlib.util.module_from_spec(spec);spec.loader.exec_module(mod)
    return mod

base=module('base_v6',M/'重剪_v6/build_v6.py');base.P=P
FF=base.FF
ANNOTATION={
 'capture':dict(duration=8,parts=[
    dict(start=294.3,length=1.9,crop='crop=2440:1120:0:440',scale='scale=1490:684',x=215,y=100),
    dict(start=296.2,length=2.2,crop='crop=2080:950:360:370',scale='scale=1740:795',x=90,y=55),
    dict(start=319.6,length=1.7,hold=2.2,crop='crop=2080:950:360:360',scale='scale=1740:795',x=90,y=55)]),
 'candidates':dict(duration=12,parts=[
    dict(start=329,length=2.4,crop='crop=2080:410:360:310',scale='scale=1740:343',x=90,y=280),
    dict(start=331.6,length=5.4,hold=4.2,crop='crop=2440:1410:0:150',scale='scale=1490:861',x=215,y=35)]),
 'confirm':dict(duration=8,parts=[dict(start=341,length=8,crop='crop=2440:1510:0:0',scale='scale=1450:897',x=235,y=23)])
}
NEW_CUES={
 'capture':[(17,.7,7.3,'先用已确认的样本训练独立画框助手，训练完成后，再选定本次使用的版本。')],
 'candidates':[(19,.6,5.7,'选定画框助手后，点击自动画框，批量生成目标候选框。'),(20,6.3,11.3,'这些候选框自动定位工件，接着再检查位置、指定类别。')],
 'confirm':[(22,.7,7.3,'检查候选框，指定姿态类别，再确认图像并进入下一张。')],
 'inference':[(25,.7,6.5,'采集区域保留原生 R G B 像素，直接送入模型推理。'),(26,7.1,13.3,'模型输出经过解码，得到工件位置和姿态类别，用于显示与后续控制。')]
}
REWORD={
 38:'类别确认并越过触发线后，按设定输出控制脉冲。',
 39:'同一身份保留过线处理状态，减少重复触发。',
 40:'按类别触发控制脉冲，再联动控制器和电磁阀，用于自动分拣。',
 47:'双向调参、原生图像推理和连续确认，共同形成视觉判断。'
}

def prepare():
    old_timeline=json.loads((OLD/'timeline.json').read_text(encoding='utf8'))
    old_records=json.loads((OLD/'配音生成记录.json').read_text(encoding='utf8'))
    timeline=[];cues=[];offset=0
    for old in old_timeline:
        s=copy.deepcopy(old);old_name=s['name'];name='inference' if old_name=='coarsefine' else old_name
        s['name']=name;s['start']=offset;s['file']=str(P/(name+'.mp4'))
        if name in ANNOTATION:s.update(duration=ANNOTATION[name]['duration'],source=dict(kind='composite',parts=ANNOTATION[name]['parts']))
        if name=='inference':s['source']=dict(kind='animation')
        timeline.append(s)
        if name in NEW_CUES:
            for identity,a,b,words in NEW_CUES[name]:cues.append(dict(id=identity,scene=name,start=offset+a,end=offset+b,spoken_text=words))
        else:
            for r in old_records:
                if r['scene']!=old_name:continue
                cues.append(dict(id=r['id'],scene=name,start=offset+r['start']-old['start'],end=offset+r['end']-old['start'],spoken_text=REWORD.get(r['id'],r['spoken_text'])))
        offset+=s['duration']
    assert offset==318
    (P/'timeline.json').write_text(json.dumps(timeline,ensure_ascii=False,indent=2),encoding='utf8')
    (P/'配音计划.json').write_text(json.dumps(dict(duration=offset,segments=cues),ensure_ascii=False,indent=2),encoding='utf8')
    print(f'prepared {offset}s / {len(cues)} speech cues',flush=True)

async def voice():
    tts=module('tts_v6',M/'重剪_v6/synthesize_voice.py');tts.P=P;tts.OUT=P/'voice';tts.OUT.mkdir(exist_ok=True);tts.RATES['inference']=0;tts.RATES['value']=0
    old={r['id']:r for r in json.loads((OLD/'配音生成记录.json').read_text(encoding='utf8'))}
    plan=json.loads((P/'配音计划.json').read_text(encoding='utf8'));records=[]
    for s in plan['segments']:
        r=old.get(s['id'])
        if r and r['spoken_text']==s['spoken_text'] and r['duration']<=s['end']-s['start']+.025:r=dict(r)
        else:r=await tts.generate(s)
        r.update(scene=s['scene'],start=s['start'],end=s['end'])
        assert r['duration']<=r['end']-r['start']+.025
        records.append(r)
        (P/'配音生成记录.json').write_text(json.dumps(records,ensure_ascii=False,indent=2),encoding='utf8')
        print(f'voice {r["id"]} {r["duration"]:.2f}s',flush=True)
    audio=np.zeros(plan['duration']*48000,np.float32)
    for r in records:
        with wave.open(r['file'],'rb') as f:a=np.frombuffer(f.readframes(f.getnframes()),dtype='<i2').astype(np.float32)/32768
        start=round(r['start']*48000);audio[start:start+len(a)]+=a
    with wave.open(str(P/'旁白_原始对轴.wav'),'wb') as f:
        f.setnchannels(1);f.setsampwidth(2);f.setframerate(48000);f.writeframes((np.clip(audio,-1,1)*32767).astype('<i2').tobytes())

def caption_text(words):
    for a,b in [('F P G A','FPGA'),('H D M I','HDMI'),('R G B','RGB'),('R K','RK')]:words=words.replace(a,b)
    font=ImageFont.truetype('C:/Windows/Fonts/msyh.ttc',44)
    if font.getlength(words)<1260:return words
    cuts=[i+1 for i,c in enumerate(words) if c in '，、；。' and 6<i<len(words)-6]
    cut=min(cuts,key=lambda i:abs(font.getlength(words[:i])-font.getlength(words[i:]))) if cuts else len(words)//2
    return words[:cut]+'\n'+words[cut:]

def captions():
    timeline=json.loads((P/'timeline.json').read_text(encoding='utf8'))
    records=json.loads((P/'配音生成记录.json').read_text(encoding='utf8'))
    all_caps=[];srt=[];checks=[]
    for s in timeline:
        ass=base.HEADER;local=[]
        for r in records:
            if r['scene']!=s['name']:continue
            text=caption_text(r['spoken_text'])
            assert re.sub(r'\s+','',text)==re.sub(r'\s+','',r['spoken_text'])
            a=max(s['start']+.15,r['start']-.12);b=min(s['start']+s['duration']-.15,r['start']+r['duration']+.18)
            style='Default';position=''
            if s['name']=='end':
                a=s['start']+.5;b=s['start']+s['duration']-.5;style='Title';position=r'\fs57\pos(960,455)'
            local.append(dict(start=a-s['start'],end=b-s['start'],text=text))
            ass+=f'Dialogue: 0,{base.ass_time(a-s["start"])},{base.ass_time(b-s["start"])},{style},,0,0,0,,{{\\fad(120,120){position}}}'+text.replace('\n',r'\N')+'\n'
            all_caps.append(dict(id=r['id'],scene=s['name'],text=text,startMs=round(a*1000),endMs=round(b*1000),timestampMs=None,confidence=None))
            srt.append(f'{len(srt)+1}\n{base.srt_time(a)} --> {base.srt_time(b)}\n{text}')
            checks.append(dict(id=r['id'],scene=s['name'],same_words=True,spoken_text=r['spoken_text'],display_text=text))
        if s['name']=='area':
            for line in (OLD/'area.ass').read_text(encoding='utf-8-sig').splitlines():
                if line.startswith('Dialogue: ') and line.split(',',9)[3]=='Title':ass+=line+'\n'
        (P/(s['name']+'.ass')).write_text(ass,encoding='utf-8-sig')
        s['captions']=local
    (P/'scenes.json').write_text(json.dumps(dict(duration=318,scenes=timeline),ensure_ascii=False,indent=2),encoding='utf8')
    (P/'captions.json').write_text(json.dumps(all_caps,ensure_ascii=False,indent=2),encoding='utf8')
    (P/'全片字幕.srt').write_text('\n\n'.join(srt)+'\n',encoding='utf-8-sig')
    (P/'字幕稿.txt').write_text('\n\n'.join(f'{base.srt_time(c["startMs"]/1000)}\n{c["text"]}' for c in all_caps),encoding='utf-8-sig')
    (P/'字幕配音逐句核对.json').write_text(json.dumps(checks,ensure_ascii=False,indent=2),encoding='utf8')
    print(f'{len(checks)} captions exactly match speech after whitespace normalization',flush=True)

def render_composite(s,preview=False):
    name=s['name'];out=P/(name+'.mp4')
    if out.exists() and not preview:return
    args=[];graph=[];labels=[]
    for i,part in enumerate(s['source']['parts']):
        args+=['-ss',str(part['start']),'-t',str(part['length']),'-i',str(base.CONSOLE)]
        dur=part['length']+part.get('hold',0)
        graph.append(f'[{i}:v]setpts=PTS-STARTPTS,{part["crop"]},{part["scale"]},pad=1920:1080:{part["x"]}:{part["y"]}:color=0x0d1d29,setsar=1,fps=60,tpad=stop_mode=clone:stop_duration={part.get("hold",0)+.1},trim=duration={dur},setpts=PTS-STARTPTS[p{i}]')
        labels.append(f'[p{i}]')
    graph.append(''.join(labels)+f'concat=n={len(labels)}:v=1:a=0,ass={name}.ass[v]')
    tail=['-frames:v','1','-q:v','2',name+'_layout.jpg'] if preview else ['-t',str(s['duration']),*base.ENC,str(out)]
    base.run([*args,'-filter_complex',';'.join(graph),'-map','[v]',*tail],name)
    print(name+' complete',flush=True)

def render_area(preview=False):
    if (P/'area.mp4').exists() and not preview:return
    graph=('[0:v]split=2[t][r];[t]crop=2560:150:0:0,scale=1100:64[top];[r]crop=1510:328:750:245,scale=1100:239[report];'
           'color=c=0x0d1d29:s=1920x1080:r=60:d=12[bg];[bg][top]overlay=760:285[b1];[b1][report]overlay=760:370,ass=area.ass[v]')
    args=['-loop','1','-framerate','60','-i',str(M/'性能工具截图/01_TD_资源利用率.png'),'-filter_complex',graph,'-map','[v]']
    base.run([*args,*(['-frames:v','1','-q:v','2','area_layout.jpg'] if preview else ['-t','12',*base.ENC,'area.mp4'])],'area')

def render(preview=False,part=None):
    for s in json.loads((P/'timeline.json').read_text(encoding='utf8')):
        if part and s['name']!=part:continue
        if s['source']['kind']=='composite':render_composite(s,preview)
        elif s['name']=='area':render_area(preview)
        else:base.render(s,preview)

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--voice',action='store_true');parser.add_argument('--captions',action='store_true');parser.add_argument('--render',action='store_true');parser.add_argument('--preview',action='store_true');parser.add_argument('--part');args=parser.parse_args()
    if args.voice:asyncio.run(voice())
    elif args.captions:captions()
    elif args.render or args.preview:render(args.preview,args.part)
    else:prepare()
