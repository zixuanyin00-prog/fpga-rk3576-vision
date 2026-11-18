from pathlib import Path
import argparse,asyncio,copy,importlib.util,json,re,wave
import numpy as np

P=Path(__file__).resolve().parent
M=P.parent

def module(name,path):
    spec=importlib.util.spec_from_file_location(name,path)
    mod=importlib.util.module_from_spec(spec);spec.loader.exec_module(mod)
    return mod

v10=module('v10_builder',M/'重剪_v10/build_v10.py')
renderer=v10.old;renderer.P=P;renderer.base.P=P
base=renderer.base;FF=base.FF

def prepare():
    previous=json.loads((M/'重剪_v11/timeline.json').read_text(encoding='utf8'))
    originals={s['name']:s for s in previous}
    old_plan=json.loads((M/'重剪_v10/配音计划.json').read_text(encoding='utf8'))['segments']
    rows=[];cues=[];offset=0
    def add(s,lines=None):
        nonlocal offset
        s=copy.deepcopy(s);old_start=s.get('start',0)
        if lines is None:lines=[(r['start']-old_start,r['end']-old_start,r['spoken_text']) for r in old_plan if r['scene']==s['name']]
        s['start']=round(offset,4);rows.append(s)
        for a,b,words in lines:cues.append(dict(id=len(cues)+1,scene=s['name'],start=round(offset+a,4),end=round(offset+b,4),spoken_text=words))
        offset+=s['duration']
    def card(name,duration):add(dict(name=name,duration=duration,file=str(P/(name+'.mp4')),source=dict(kind='animation')),[])
    add(dict(name='intro',duration=4,file=str(P/'intro.mp4'),source=dict(kind='footage',file='电阻姿态展示.MOV',start=1,length=4)),[(.35,3.7,'这些工件，怎样自动分开？')])
    card('chapter_board',1.5)
    board=str(M/'板卡移到振动盘.MOV');control=str(M/'电磁阀和转接板展示.MOV')
    add(dict(name='rk_board',duration=5.5,file=str(P/'rk_board.mp4'),source=dict(kind='composite',parts=[dict(file=board,start=0,length=5.5,crop='crop=1660:680:970:850',scale='scale=1740:714',x=90,y=45)])),[(.4,5.1,'R K 三五七六，负责模型识别和显示。')])
    add(dict(name='fpga_board',duration=5,file=str(P/'fpga_board.mp4'),source=dict(kind='composite',parts=[dict(file=board,start=2.5,length=5,crop='crop=2100:1180:740:120',scale='scale=1494:840',x=213,y=28)])),[(.3,4.7,'安路 F P G A 负责图像采集和预处理。')])
    add(dict(name='control_board',duration=5,file=str(P/'control_board.mp4'),source=dict(kind='composite',parts=[
        dict(file=control,start=0,length=2.8,crop='crop=3840:2160:0:0',scale='scale=1600:900',x=160,y=0),
        dict(file=control,start=5.1,length=2.2,crop='crop=3840:2160:0:0',scale='scale=1600:900',x=160,y=0)])),[(.3,4.7,'控制板接收控制脉冲，联动电磁阀筛选。')])
    add(originals['station'])
    card('chapter_console',1.5)
    for name in ['console_image','parameter_short','console_annotate','console_train','console_run']:add(originals[name])
    card('chapter_logic',1.5)
    for name in ['flow_short','tracking_short','trigger_short']:add(originals[name])
    card('application',3)
    add(originals['app_pose'])
    material=copy.deepcopy(originals['app_compare']);material['name']='material_prepare'
    add(material,[(.5,6,'先展示本次类别实验使用的两种电阻：四七三和一零零四。'),(6.5,11.6,'本次设定：剔除四七三，保留一零零四。')])
    add(originals['app_hdmi'])
    add(originals['app_track'],[(.4,6.4,'工件沿轨道依次通过，按设定类别完成分拣。')])
    for name in ['app_execute','app_collect','end']:add(originals[name])
    duration=round(offset,4);assert duration==171.8 and duration<180
    (P/'timeline.json').write_text(json.dumps(rows,ensure_ascii=False,indent=2),encoding='utf8')
    (P/'配音计划.json').write_text(json.dumps(dict(duration=duration,segments=cues),ensure_ascii=False,indent=2),encoding='utf8')
    for s in rows:
        if Path(s['file']).parent==P:(P/(s['name']+'.ass')).write_text(base.HEADER,encoding='utf-8-sig')
    chapters=[dict(title='引子',start=0),dict(title='01 板卡介绍',start=rows[1]['start'])]
    for s in rows:
        if s['name']=='chapter_console':chapters.append(dict(title='02 控制台操作',start=s['start']))
        elif s['name']=='chapter_logic':chapters.append(dict(title='03 识别与控制',start=s['start']))
        elif s['name']=='application':chapters.append(dict(title='04 实战与应用',start=s['start']))
    metadata=';FFMETADATA1\n'
    for i,c in enumerate(chapters):
        c['end']=chapters[i+1]['start'] if i+1<len(chapters) else duration
        metadata+=f'[CHAPTER]\nTIMEBASE=1/1000\nSTART={round(c["start"]*1000)}\nEND={round(c["end"]*1000)}\ntitle={c["title"]}\n'
    (P/'chapters.ffmeta').write_text(metadata,encoding='utf8')
    (P/'章节时间.json').write_text(json.dumps(chapters,ensure_ascii=False,indent=2),encoding='utf8')
    print(f'prepared {duration}s / {len(rows)} scenes / {len(cues)} speech cues',flush=True)

async def voice():
    tts=module('tts_v12',M/'重剪_v6/synthesize_voice.py');tts.P=P;tts.OUT=P/'voice';tts.OUT.mkdir(exist_ok=True)
    for f in tts.OUT.glob('*.mp3'):
        if f.stat().st_size==0:f.unlink()
    plan=json.loads((P/'配音计划.json').read_text(encoding='utf8'))
    previous=json.loads((M/'重剪_v10/配音生成记录.json').read_text(encoding='utf8'))
    sem=asyncio.Semaphore(2)
    async def one(s):
        tts.RATES[s['scene']]=0
        r=next((dict(r) for r in previous if r['spoken_text']==s['spoken_text'] and r['duration']<=s['end']-s['start']),None)
        if r is None:
            async with sem:r=await tts.generate(s)
        r.update(id=s['id'],scene=s['scene'],start=s['start'],end=s['end'])
        print(f'voice {s["id"]}: {r["duration"]:.2f}s rate {r["rate"]}',flush=True)
        return r
    records=await asyncio.gather(*(one(s) for s in plan['segments']))
    (P/'配音生成记录.json').write_text(json.dumps(records,ensure_ascii=False,indent=2),encoding='utf8')
    audio=np.zeros(round(plan['duration']*48000),np.float32)
    for r in records:
        with wave.open(r['file'],'rb') as f:a=np.frombuffer(f.readframes(f.getnframes()),dtype='<i2').astype(np.float32)/32768
        start=round(r['start']*48000);audio[start:start+len(a)]+=a
    with wave.open(str(P/'旁白_原始对轴.wav'),'wb') as f:
        f.setnchannels(1);f.setsampwidth(2);f.setframerate(48000);f.writeframes((np.clip(audio,-1,1)*32767).astype('<i2').tobytes())

def normalize(words):return v10.normalize(words.replace('三五七六','3576'))

def captions():
    records=json.loads((P/'配音生成记录.json').read_text(encoding='utf8'))
    timeline=json.loads((P/'timeline.json').read_text(encoding='utf8'))
    caps=[];checks=[];srt=[];labels=[];ass=base.HEADER
    ass=ass.replace('[Events]','Style: Rule,Microsoft YaHei,32,&H00F2E3BF,&H00F2E3BF,&HAA291D0D,&HAA291D0D,0,0,0,0,100,100,0.8,0,3,9,0,2,100,100,153,1\n[Events]')
    for r in records:
        s=next(s for s in timeline if s['name']==r['scene'])
        words=r['spoken_text'].replace('四七三','473').replace('一零零四','1004').replace('三五七六','3576')
        text=renderer.v8.caption_text(words)
        assert normalize(text)==normalize(r['spoken_text'])
        a=max(s['start']+.15,r['start']-.12);b=min(s['start']+s['duration']-.15,r['start']+r['duration']+.18)
        style='Default';position=''
        if s['name']=='end':a=s['start']+.5;b=s['start']+s['duration']-.5;style='Title';position=r'\fs57\pos(960,455)'
        ass+=f'Dialogue: 1,{base.ass_time(a)},{base.ass_time(b)},{style},,0,0,0,,{{\\fad(120,120){position}}}'+text.replace('\n',r'\N')+'\n'
        caps.append(dict(id=r['id'],scene=s['name'],text=text,startMs=round(a*1000),endMs=round(b*1000),timestampMs=None,confidence=None))
        checks.append(dict(id=r['id'],same_words=True,spoken_text=r['spoken_text'],display_text=text))
        srt.append(f'{len(srt)+1}\n{base.srt_time(a)} --> {base.srt_time(b)}\n{text}')
    specific={'rk_board':'RK3576｜识别主机','fpga_board':'安路FPGA｜PH1A90采集处理卡','control_board':'控制板｜电磁阀控制转接模块','material_prepare':'实验材料　473 · 1004'}
    for s in timeline:
        name=s['name']
        if name in specific:text=specific[name]
        elif name=='app_pose':text='姿态分拣　剔除白色反面电阻'
        elif name.startswith('app_'):text='类别分拣　剔除473 · 保留1004'
        else:continue
        a=s['start']+.15;b=s['start']+s['duration']-.15
        ass+=f'Dialogue: 0,{base.ass_time(a)},{base.ass_time(b)},Rule,,0,0,0,,{{\\fad(120,120)}}{text}\n'
        labels.append(dict(scene=name,text=text,start=a,end=b))
    ass+='Dialogue: 0,0:00:00.15,0:00:03.80,Small,,0,0,0,,{\\fad(150,150)\\pos(960,110)\\fs34}引子\n'
    ass+='Dialogue: 0,0:00:00.15,0:00:03.80,Title,,0,0,0,,{\\fad(150,150)\\pos(960,188)\\fs58}让工件自动分开\n'
    (P/'final.ass').write_text(ass,encoding='utf-8-sig')
    (P/'captions.json').write_text(json.dumps(caps,ensure_ascii=False,indent=2),encoding='utf8')
    (P/'分拣规则与板卡标识.json').write_text(json.dumps(labels,ensure_ascii=False,indent=2),encoding='utf8')
    (P/'全片字幕.srt').write_text('\n\n'.join(srt)+'\n',encoding='utf-8-sig')
    (P/'字幕配音逐句核对.json').write_text(json.dumps(checks,ensure_ascii=False,indent=2),encoding='utf8')
    print(f'{len(caps)} unified captions and {len(labels)} scene labels',flush=True)

def render():
    timeline=json.loads((P/'timeline.json').read_text(encoding='utf8'))
    for s in timeline:
        if s['name']=='intro':
            out=P/'intro.mp4'
            if out.exists():continue
            graph='[0:v]setpts=PTS-STARTPTS,scale=1920:1080:force_original_aspect_ratio=increase,crop=1920:1080,drawbox=x=0:y=0:w=iw:h=268:color=black@0.40:t=fill,drawbox=x=0:y=938:w=iw:h=142:color=black@0.24:t=fill,setsar=1,fps=60[v]'
            base.run(['-ss','1','-t','4.1','-i',str(M/'电阻姿态展示.MOV'),'-filter_complex',graph,'-map','[v]','-frames:v','240',*base.ENC,str(out)],'intro')
        elif s['name'] in ['rk_board','fpga_board','control_board']:renderer.render_composite(s)

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--voice',action='store_true');parser.add_argument('--captions',action='store_true');parser.add_argument('--render',action='store_true');args=parser.parse_args()
    if args.voice:asyncio.run(voice())
    elif args.captions:captions()
    elif args.render:render()
    else:prepare()
