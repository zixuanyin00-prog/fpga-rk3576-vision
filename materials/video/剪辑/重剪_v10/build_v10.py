from pathlib import Path
import argparse,asyncio,copy,importlib.util,json,re,wave
import numpy as np

P=Path(__file__).resolve().parent
M=P.parent

def module(name,path):
    spec=importlib.util.spec_from_file_location(name,path)
    mod=importlib.util.module_from_spec(spec);spec.loader.exec_module(mod)
    return mod

old=module('v9_builder',M/'重剪_v9/build_v9.py')
old.P=P;old.base.P=P
base=old.base;FF=base.FF

ANIMATION_LINES={
 'parameter_short':(6,[(.4,5.6,'参数经 R K 下发，F P G A 回传状态，实现双向调参。')]),
 'flow_short':(4,[(.3,3.7,'从图像采集，到识别和执行。')]),
 'tracking_short':(7,[(.5,6.5,'跨帧关联同一工件，结合连续真实观测，确认类别。')]),
 'trigger_short':(7,[(.4,6.6,'类别确认后，工件实际过线，按设定延时和脉宽输出脉冲。')]),
}
LINES={
 'pose_intro':[(.4,5.1,'白色面是电阻反面，在姿态分拣中需要剔除。')],
 'app_hdmi':[(.4,6.6,'类别分拣中，剔除四七三电阻，保留一零零四电阻。')],
 'app_pose':[(.5,6.8,'姿态分拣中，白色反面电阻被识别后剔除。')],
 'app_track':[(.4,6.4,'这里切换到类别分拣，剔除四七三，保留一零零四。')],
 'app_execute':[(.6,6.8,'类别确认并实际过线后，控制器驱动电磁阀，剔除四七三。'),
                (8.2,15.2,'一零零四电阻继续沿轨道通过，进入后续收集。'),
                (17,23.4,'连续识别、确认和执行衔接，让两种型号自动分开。')],
 'app_collect':[(.4,8.1,'从收集杯中取出保留的一零零四电阻，近看表面型号。')],
 'app_compare':[(.5,6,'对照结果，四七三被剔除，一零零四保留下来。'),
                (6.5,11.6,'分类规则可随任务调整，用现场样本重新训练。')],
}

def prepare():
    timeline=json.loads((M/'重剪_v9/timeline.json').read_text(encoding='utf8'))
    old_plan=json.loads((M/'重剪_v9/配音计划.json').read_text(encoding='utf8'))['segments']
    rows=[];cues=[];offset=0
    def add(s,lines):
        nonlocal offset
        s['start']=round(offset,4);rows.append(s)
        for a,b,words in lines:
            cues.append(dict(id=len(cues)+1,scene=s['name'],start=round(offset+a,4),end=round(offset+b,4),spoken_text=words))
        offset+=s['duration']
    def animation(name):
        duration,lines=ANIMATION_LINES[name]
        add(dict(name=name,duration=duration,file=str(P/(name+'.mp4')),source=dict(kind='animation')),lines)
    for previous in timeline:
        name=previous['name']
        if name=='lighttech':
            for item in ['flow_short','tracking_short','trigger_short']:animation(item)
            continue
        s=copy.deepcopy(previous)
        if name=='app_collect':
            s['duration']=8.5;s['file']=str(P/'app_collect.mp4')
            s['source']['parts'][1].update(length=3.4)
            s['source']['parts'][1].pop('hold',None)
        lines=LINES.get(name,[(r['start']-previous['start'],r['end']-previous['start'],r['spoken_text']) for r in old_plan if r['scene']==name])
        add(s,lines)
        if name=='console_image':animation('parameter_short')
    assert round(offset,3)==162.3 and offset<180
    (P/'timeline.json').write_text(json.dumps(rows,ensure_ascii=False,indent=2),encoding='utf8')
    (P/'配音计划.json').write_text(json.dumps(dict(duration=round(offset,4),segments=cues),ensure_ascii=False,indent=2),encoding='utf8')
    for s in rows:
        if Path(s['file']).parent==P:(P/(s['name']+'.ass')).write_text(base.HEADER,encoding='utf-8-sig')
    print(f'prepared {offset:.3f}s / {len(cues)} cues',flush=True)

async def voice():
    tts=module('tts_v10',M/'重剪_v6/synthesize_voice.py');tts.P=P;tts.OUT=P/'voice';tts.OUT.mkdir(exist_ok=True)
    for f in tts.OUT.glob('*.mp3'):
        if f.stat().st_size==0:f.unlink()
    plan=json.loads((P/'配音计划.json').read_text(encoding='utf8'))
    previous=json.loads((M/'重剪_v9/配音生成记录.json').read_text(encoding='utf8'))
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

def normalize(words):
    for a,b in [('F P G A','FPGA'),('H D M I','HDMI'),('R G B','RGB'),('R K','RK'),('四七三','473'),('一零零四','1004')]:words=words.replace(a,b)
    return re.sub(r'\s+','',words)

def captions():
    records=json.loads((P/'配音生成记录.json').read_text(encoding='utf8'))
    timeline=json.loads((P/'timeline.json').read_text(encoding='utf8'))
    caps=[];checks=[];srt=[];rules=[];ass=base.HEADER
    rule_style='Style: Rule,Microsoft YaHei,32,&H00F2E3BF,&H00F2E3BF,&HAA291D0D,&HAA291D0D,0,0,0,0,100,100,0.8,0,3,9,0,2,100,100,153,1\n'
    ass=ass.replace('[Events]',rule_style+'[Events]')
    for r in records:
        s=next(s for s in timeline if s['name']==r['scene'])
        words=r['spoken_text'].replace('四七三','473').replace('一零零四','1004')
        text=old.v8.caption_text(words)
        assert normalize(text)==normalize(r['spoken_text'])
        a=max(s['start']+.15,r['start']-.12);b=min(s['start']+s['duration']-.15,r['start']+r['duration']+.18)
        style='Default';position=''
        if s['name']=='end':a=s['start']+.5;b=s['start']+s['duration']-.5;style='Title';position=r'\fs57\pos(960,455)'
        ass+=f'Dialogue: 1,{base.ass_time(a)},{base.ass_time(b)},{style},,0,0,0,,{{\\fad(120,120){position}}}'+text.replace('\n',r'\N')+'\n'
        caps.append(dict(id=r['id'],scene=s['name'],text=text,startMs=round(a*1000),endMs=round(b*1000),timestampMs=None,confidence=None))
        checks.append(dict(id=r['id'],same_words=True,spoken_text=r['spoken_text'],display_text=text))
        srt.append(f'{len(srt)+1}\n{base.srt_time(a)} --> {base.srt_time(b)}\n{text}')
    for s in timeline:
        if not (s['name'].startswith('app_') or s['name']=='pose_intro'):continue
        if s['name']=='app_pose':text='姿态分拣　剔除白色反面电阻'
        elif s['name']=='pose_intro':text='姿态示例　白色面为反面'
        else:text='类别分拣　剔除473 · 保留1004'
        a=s['start']+.15;b=s['start']+s['duration']-.15
        ass+=f'Dialogue: 0,{base.ass_time(a)},{base.ass_time(b)},Rule,,0,0,0,,{{\\fad(120,120)}}{text}\n'
        rules.append(dict(scene=s['name'],text=text,start=a,end=b))
    (P/'final.ass').write_text(ass,encoding='utf-8-sig')
    (P/'captions.json').write_text(json.dumps(caps,ensure_ascii=False,indent=2),encoding='utf8')
    (P/'分拣规则标识.json').write_text(json.dumps(rules,ensure_ascii=False,indent=2),encoding='utf8')
    (P/'全片字幕.srt').write_text('\n\n'.join(srt)+'\n',encoding='utf-8-sig')
    (P/'字幕配音逐句核对.json').write_text(json.dumps(checks,ensure_ascii=False,indent=2),encoding='utf8')
    print(f'{len(caps)} unified captions and {len(rules)} task labels',flush=True)

def render():
    for s in json.loads((P/'timeline.json').read_text(encoding='utf8')):
        if s['name']=='app_collect':old.render_composite(s)

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--voice',action='store_true');parser.add_argument('--captions',action='store_true');parser.add_argument('--render',action='store_true');args=parser.parse_args()
    if args.voice:asyncio.run(voice())
    elif args.captions:captions()
    elif args.render:render()
    else:prepare()
