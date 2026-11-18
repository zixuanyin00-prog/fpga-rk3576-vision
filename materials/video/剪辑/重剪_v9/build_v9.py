from pathlib import Path
import argparse, asyncio, functools, importlib.util, json, re, subprocess, urllib.request, wave
import numpy as np

P=Path(__file__).resolve().parent
M=P.parent

def module(name,path):
    spec=importlib.util.spec_from_file_location(name,path)
    mod=importlib.util.module_from_spec(spec);spec.loader.exec_module(mod)
    return mod

v8=module('v8_builder',M/'重剪_v8/build_v8.py')
base=v8.base;base.P=P
FF=base.FF

def prepare():
    raw=str(base.CONSOLE)
    rows=[];cues=[];offset=0
    definitions=[
        ('title',4,dict(kind='animation'),[]),
        ('board',5,dict(base.SOURCES['board'],length=5),[(.4,4.6,'F P G A 负责前端采集和图像预处理。')]),
        ('station',11,dict(base.SOURCES['new_pan']),[(.5,10.3,'以电阻为例，相机采集运动工件，识别结果进入后续分拣流程。')]),
        ('pose_intro',5.5,dict(base.SOURCES['pose'],length=5.5),[(.4,5.1,'姿态标签可区分正反面和数字方向。')]),
        ('console_image',10,dict(kind='syncparts',parts=[dict(base.SOURCES['isp_overview'],length=4,detail='crop=1320:270:365:1330'),dict(base.SOURCES['isp_processing'],start=102,length=6)]),[(.5,9.5,'画面页调整成像与采集区域，参数下发后回读状态，便于现场调节。')]),
        ('console_annotate',12,dict(kind='composite',parts=[
            dict(file=raw,start=329,length=2.4,crop='crop=2080:410:360:310',scale='scale=1740:343',x=90,y=280),
            dict(file=raw,start=331.6,length=5.4,crop='crop=2440:1410:0:150',scale='scale=1490:861',x=215,y=35),
            dict(file=raw,start=341,length=4.2,crop='crop=2440:1510:0:0',scale='scale=1450:897',x=235,y=23)]),
            [(.4,11.6,'标注页用已确认样本训练并选定画框助手，一键生成候选框，再检查位置、指定类别。')]),
        ('console_train',7,dict(kind='composite',parts=[
            dict(file=raw,start=496,length=4,crop='crop=2440:820:0:0',scale='scale=1740:584',x=90,y=183),
            dict(file=raw,start=520,length=3,crop='crop=2440:1180:0:0',scale='scale=1710:826',x=105,y=53)]),
            [(.4,6.6,'训练页完成训练和模型转换，并用现场图像检查识别结果。')]),
        ('console_run',7,dict(base.SOURCES['deploy']),[(.4,6.6,'运行页监控实时画面、识别结果和设备状态，供现场检查。')]),
        ('lighttech',10,dict(kind='animation'),[(.5,9.5,'显示与推理异步运行，再通过跨帧关联、连续确认，把识别结果连接到外部控制。')]),
        ('application',3,dict(kind='animation'),[]),
        ('app_hdmi',7,dict(kind='footage',file='类别分拣 (2).mp4',start=.2,length=7),[(.4,6.6,'屏幕给出目标位置和类别，作为后续分拣的判断依据。')]),
        ('app_pose',10.5,dict(kind='footage',file='姿态分拣.mp4',start=.3,length=10.5),[(.5,6.8,'先看姿态分拣，工件在送料轨道上连续通过识别区域。')]),
        ('app_track',6.8,dict(kind='footage',file='0-7s可用.MP4',start=.1,length=6.8),[(.4,6.4,'工件实际越过触发线后，按选定类别输出控制脉冲。')]),
        ('app_execute',24,dict(kind='footage',file='30-56s可用.MP4',start=32,length=24),[
            (.6,6.8,'控制器驱动电磁阀，在执行位置完成选择。'),
            (8.2,15.2,'连续识别、确认与执行衔接，让工件随送料过程自动分流。'),
            (17,23.4,'更换分类任务时，可以重新定义标签，并用现场样本训练。')]),
        ('app_collect',11,dict(kind='composite',parts=[
            dict(file=str(M/'类别分拣结果展示.mp4'),start=.2,length=5.1),
            dict(file=str(M/'类别分拣结果展示.mp4'),start=6.6,length=3.2,hold=2.7)]),
            [(.5,6,'完成一轮分拣后，从收集杯中取出工件。'),(6.3,10.6,'查看实物表面的型号，对照识别与分拣结果。')]),
        ('app_compare',12,dict(kind='compare',file='类别分拣.mp4',start=0,length=12),[
            (.5,6,'类别也可以对应不同型号，这里以两种电阻为例。'),
            (6.5,11.6,'从姿态筛选到型号分类，同一套视觉流程服务不同分拣任务。')]),
        ('end',5,dict(kind='animation'),[(.7,4.3,'让视觉判断，服务自动分拣。')]),
    ]
    for name,duration,source,lines in definitions:
        rows.append(dict(name=name,start=round(offset,4),duration=duration,file=str(P/(name+'.mp4')),source=source))
        for a,b,text in lines:
            cues.append(dict(id=len(cues)+1,scene=name,start=round(offset+a,4),end=round(offset+b,4),spoken_text=text))
        offset+=duration
    assert offset<180
    plan=dict(duration=round(offset,4),segments=cues)
    (P/'timeline.json').write_text(json.dumps(rows,ensure_ascii=False,indent=2),encoding='utf8')
    (P/'配音计划.json').write_text(json.dumps(plan,ensure_ascii=False,indent=2),encoding='utf8')
    for row in rows:(P/(row['name']+'.ass')).write_text(base.HEADER,encoding='utf-8-sig')
    print(f'prepared {offset:.3f}s / {len(cues)} cues',flush=True)

async def voice():
    tts=module('tts_v9',M/'重剪_v6/synthesize_voice.py');tts.P=P;tts.OUT=P/'voice';tts.OUT.mkdir(exist_ok=True)
    proxy=urllib.request.getproxies().get('https')
    if proxy:tts.edge_tts.Communicate=functools.partial(tts.edge_tts.Communicate,proxy=proxy)
    for f in tts.OUT.glob('*.mp3'):
        if f.stat().st_size==0:f.unlink()
    plan=json.loads((P/'配音计划.json').read_text(encoding='utf8'))
    previous=json.loads((M/'重剪_v8/配音生成记录.json').read_text(encoding='utf8'))
    sem=asyncio.Semaphore(2)
    async def one(s):
        tts.RATES[s['scene']]=0
        reuse=next((dict(r) for r in previous if r['spoken_text']==s['spoken_text'] and r['duration']<=s['end']-s['start']),None)
        if reuse:r=reuse
        else:
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

def captions():
    records=json.loads((P/'配音生成记录.json').read_text(encoding='utf8'))
    timeline=json.loads((P/'timeline.json').read_text(encoding='utf8'))
    caps=[];checks=[];ass=base.HEADER;srt=[]
    for r in records:
        s=next(s for s in timeline if s['name']==r['scene'])
        text=v8.caption_text(r['spoken_text'])
        assert re.sub(r'\s+','',text)==re.sub(r'\s+','',r['spoken_text'])
        a=max(s['start']+.15,r['start']-.12);b=min(s['start']+s['duration']-.15,r['start']+r['duration']+.18)
        style='Default';position=''
        if s['name']=='end':a=s['start']+.5;b=s['start']+s['duration']-.5;style='Title';position=r'\fs57\pos(960,455)'
        ass+=f'Dialogue: 0,{base.ass_time(a)},{base.ass_time(b)},{style},,0,0,0,,{{\\fad(120,120){position}}}'+text.replace('\n',r'\N')+'\n'
        caps.append(dict(id=r['id'],scene=s['name'],text=text,startMs=round(a*1000),endMs=round(b*1000),timestampMs=None,confidence=None))
        checks.append(dict(id=r['id'],same_words=True,spoken_text=r['spoken_text'],display_text=text))
        srt.append(f'{len(srt)+1}\n{base.srt_time(a)} --> {base.srt_time(b)}\n{text}')
    (P/'final.ass').write_text(ass,encoding='utf-8-sig')
    (P/'captions.json').write_text(json.dumps(caps,ensure_ascii=False,indent=2),encoding='utf8')
    (P/'全片字幕.srt').write_text('\n\n'.join(srt)+'\n',encoding='utf-8-sig')
    (P/'字幕配音逐句核对.json').write_text(json.dumps(checks,ensure_ascii=False,indent=2),encoding='utf8')
    (P/'scenes.json').write_text(json.dumps(dict(duration=sum(s['duration'] for s in timeline),scenes=timeline),ensure_ascii=False,indent=2),encoding='utf8')
    print(f'{len(caps)} unified captions prepared',flush=True)

def render_composite(s):
    name=s['name'];out=P/(name+'.mp4')
    if out.exists():return
    args=[];graph=[];labels=[]
    for i,part in enumerate(s['source']['parts']):
        args+=['-ss',str(part['start']),'-t',str(part['length']),'-i',str(part['file'])]
        dur=part['length']+part.get('hold',0)
        if 'crop' in part:layout=f'{part["crop"]},{part["scale"]},pad=1920:1080:{part["x"]}:{part["y"]}:color=0x0d1d29'
        else:layout='scale=1920:1080:force_original_aspect_ratio=increase,crop=1920:1080,drawbox=x=0:y=938:w=iw:h=142:color=black@0.24:t=fill'
        graph.append(f'[{i}:v]setpts=PTS-STARTPTS,{layout},setsar=1,fps=60,tpad=stop_mode=clone:stop_duration={part.get("hold",0)+.1},trim=duration={dur},setpts=PTS-STARTPTS[p{i}]')
        labels.append(f'[p{i}]')
    graph.append(''.join(labels)+f'concat=n={len(labels)}:v=1:a=0,ass={name}.ass[v]')
    base.run([*args,'-filter_complex',';'.join(graph),'-map','[v]','-frames:v',str(round(s['duration']*60)),*base.ENC,str(out)],name)
    print(name+' complete',flush=True)

def render_sync(s):
    name=s['name'];out=P/(name+'.mp4')
    if out.exists():return
    inputs=[];graph=[];labels=[]
    for i,p in enumerate(s['source']['parts']):
        n=i*2;dur=p['length']
        inputs+=['-ss',str(p['start']),'-t',str(dur),'-i',str(base.CONSOLE),'-ss',str(p['start']),'-t',str(dur),'-i',str(M/'实拍ISP使用.MOV')]
        graph += [f'[{n}:v]setpts=PTS-STARTPTS,fps=60,split=2[c{i}][d{i}]',
            f'[c{i}]{p["context"]},setsar=1[ctx{i}]',
            f'[d{i}]{p["detail"]},scale=600:{p["detail_h"]},setsar=1[detail{i}]',
            f'[{n+1}:v]setpts=PTS-STARTPTS,fps=60,crop=1800:1012:60:34,scale=1200:674,setsar=1[hdmi{i}]',
            f'color=c=0x0d1d29:s=1920x1080:r=60:d={dur}[bg{i}]',
            f'[bg{i}]drawbox=x=26:y=142:w=608:h=360:color=0x647f82:t=2,drawbox=x=26:y={p["detail_y"]-4}:w=608:h={p["detail_h"]+8}:color=0x647f82:t=2,drawbox=x=656:y=158:w=1208:h=682:color=0xabc9bd:t=2[b{i}]',
            f'[b{i}][ctx{i}]overlay=30:146[u{i}]',
            f'[u{i}][detail{i}]overlay=30:{p["detail_y"]}[v{i}]',
            f'[v{i}][hdmi{i}]overlay=660:162,trim=duration={dur},setpts=PTS-STARTPTS[p{i}]']
        labels.append(f'[p{i}]')
    graph.append(''.join(labels)+f'concat=n={len(labels)}:v=1:a=0,ass={name}.ass[v]')
    base.run([*inputs,'-filter_complex',';'.join(graph),'-map','[v]','-frames:v',str(round(s['duration']*60)),*base.ENC,str(out)],name)
    print(name+' complete',flush=True)

def render_compare(s):
    name=s['name'];out=P/(name+'.mp4')
    if out.exists():return
    graph=('[0:v]setpts=PTS-STARTPTS,split=2[full][detail];'
        '[full]scale=450:800,setsar=1[f];[detail]crop=2000:1320:80:520,scale=1240:818,setsar=1[d];'
        f'color=c=0x0d1d29:s=1920x1080:r=60:d={s["duration"]}[bg];'
        '[bg]drawbox=x=78:y=63:w=454:h=804:color=0x355768:t=2,drawbox=x=598:y=54:w=1244:h=822:color=0x355768:t=2[b];'
        f'[b][f]overlay=80:65[b1];[b1][d]overlay=600:56,setsar=1,fps=60,ass={name}.ass[v]')
    base.run(['-ss',str(s['source']['start']),'-t',str(s['duration']),'-i',str(M/s['source']['file']),'-filter_complex',graph,'-map','[v]','-frames:v',str(round(s['duration']*60)),*base.ENC,str(out)],name)
    print(name+' complete',flush=True)

def render(part=None):
    for s in json.loads((P/'timeline.json').read_text(encoding='utf8')):
        if part and s['name']!=part:continue
        (P/(s['name']+'.ass')).write_text(base.HEADER,encoding='utf-8-sig')
        kind=s['source']['kind']
        if kind=='animation':continue
        if kind=='composite':render_composite(s)
        elif kind=='syncparts':render_sync(s)
        elif kind=='compare':render_compare(s)
        else:base.render(s)

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--voice',action='store_true');parser.add_argument('--captions',action='store_true');parser.add_argument('--render',action='store_true');parser.add_argument('--part');args=parser.parse_args()
    if args.voice:asyncio.run(voice())
    elif args.captions:captions()
    elif args.render:render(args.part)
    else:prepare()
