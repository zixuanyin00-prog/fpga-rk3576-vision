from pathlib import Path
import argparse, asyncio, copy, importlib.util, json, math, subprocess, wave
import numpy as np

P=Path(__file__).resolve().parent
OLD=P.parent/'重剪_v6'
spec=importlib.util.spec_from_file_location('v6',OLD/'build_v6.py')
v6=importlib.util.module_from_spec(spec);spec.loader.exec_module(v6)
v6.P=P
CHANGED={'pose','value','hardware','area','collage','end'}
CHANGES={
 'pose':[(.5,7.5,'以电阻为例，系统辨别正反面与数字方向，\n为后续取放、筛选提供明确的类别信息。')],
 'value':[(.5,7.5,'控制台中的“姿态”标签可按任务自定义，\n不同型号的电阻，也可设为不同识别类别。'),(7.8,15.5,'与控制器联动后，可按类别触发分拣，\n服务于高速、高精度的自动分类需求。')],
 'hardware':[(.5,7.5,'识别事件可接入后续控制器，\n按类别驱动电磁阀，完成对应的分拣动作。')],
 'area':[(.5,5.8,'图像处理落实到 FPGA，\n安路 TD 报告记录了设计的硬件资源占用。'),(6,11.5,'报告中 LUT6 占用为 73.57%，\n对应 47,319 个六输入查找表。')],
}
SPOKEN={
 ('pose',1):'以电阻为例，先辨别正反面和数字方向，为后续取放、筛选提供类别信息。',
 ('value',1):'姿态标签可以按任务自定义，不同型号的电阻，也能设为不同识别类别。',
 ('value',2):'结合后续控制器，按类别触发分拣，面向高速、高精度的自动分类应用。',
 ('hardware',1):'识别事件接入后续控制器，按类别驱动电磁阀，完成对应的分拣动作。',
 ('area',1):'图像处理落实到 F P G A，安路工具报告记录了硬件资源占用。',
 ('area',2):'报告显示，六输入查找表占用百分之七十三点五七。',
 ('collage',4):'从识别姿态到区分型号，同一套视觉流程，服务不同工件的分类需求。',
 ('end',1):'让视觉判断，服务自动分拣。'
}

def prepare():
    scenes=json.loads((OLD/'scenes.json').read_text(encoding='utf8'))['scenes']
    scenes.insert(2,dict(name='value',duration=16,captions=[]))
    old_timeline={s['name']:s for s in json.loads((OLD/'时间线.json').read_text(encoding='utf8'))}
    timeline=[];captions=[];offset=0;srt=[];lookup={}
    for s in scenes:
        name=s['name'];dur=s['duration']
        if name in CHANGES:s['captions']=[dict(start=a,end=b,text=t) for a,b,t in CHANGES[name]]
        if name=='collage':s['captions'][-1]['text']='从识别姿态到区分型号，\n让同一套视觉流程适配不同工件的分类需求。'
        item=copy.deepcopy(old_timeline[name]) if name in old_timeline else dict(name=name,duration=dur,source=dict(kind='animation'))
        item['start']=offset;item['file']=str((P if name in CHANGED else OLD)/(name+'.mp4'))
        timeline.append(item)
        ass=v6.HEADER
        for i,c in enumerate(s['captions'],1):
            a,b,t=c['start'],c['end'],c['text']
            ass+=f'Dialogue: 0,{v6.ass_time(a)},{v6.ass_time(b)},Default,,0,0,0,,{{\\fad(160,160)}}'+t.replace('\n',r'\N')+'\n'
            cap=dict(text=t,startMs=round((offset+a)*1000),endMs=round((offset+b)*1000),timestampMs=None,confidence=None)
            captions.append(cap)
            srt.append(f'{len(srt)+1}\n{v6.srt_time(offset+a)} --> {v6.srt_time(offset+b)}\n{t}')
            lookup[(name,i)]=(offset+a,offset+b,t)
        if name=='area':
            for text,size,x,y,col in [('FPGA 硬件实现',48,960,116,'F3F5ED'),('73.57%',102,360,445,'CBDF78'),('LUT6 资源占用',31,360,343,'F3F5ED'),('47,319 / 64,320',28,360,563,'C7BDA5')]:
                ass+=f'Dialogue: 0,0:00:00.00,0:00:12.00,Title,,0,0,0,,{{\\fs{size}\\pos({x},{y})\\c&H{col}&}}{text}\n'
        (P/(name+'.ass')).write_text(ass,encoding='utf-8-sig')
        offset+=dur
    assert offset==326
    (P/'scenes.json').write_text(json.dumps(dict(duration=offset,scenes=scenes),ensure_ascii=False,indent=2),encoding='utf8')
    (P/'timeline.json').write_text(json.dumps(timeline,ensure_ascii=False,indent=2),encoding='utf8')
    (P/'captions.json').write_text(json.dumps(captions,ensure_ascii=False,indent=2),encoding='utf8')
    (P/'全片字幕.srt').write_text('\n\n'.join(srt)+'\n',encoding='utf-8-sig')
    (P/'字幕稿.txt').write_text('\n\n'.join(f'{v6.srt_time(c["startMs"]/1000)}\n{c["text"]}' for c in captions),encoding='utf-8-sig')
    plan=json.loads((OLD/'配音计划_最终.json').read_text(encoding='utf8'))
    for s in plan['segments']:
        key=(s['scene'],s['scene_caption_index'])
        if s['scene']=='end':a,b,t=320.5,325.5,'让视觉判断，服务自动分拣。'
        else:a,b,t=lookup[key]
        s.update(caption_start=a,caption_end=b,start=a+.2,end=b-.2,text=t)
        if s['scene']=='end':s.update(start=320.9,end=325.1)
        if key in SPOKEN:s['spoken_text']=SPOKEN[key]
    for i in [1,2]:
        a,b,t=lookup[('value',i)]
        plan['segments'].append(dict(id=49+i,scene='value',scene_caption_index=i,caption_start=a,caption_end=b,start=a+.2,end=b-.2,text=t,spoken_text=SPOKEN[('value',i)]))
    plan['segments'].sort(key=lambda s:s['start']);plan['duration']=offset
    plan.pop('production_notes',None)
    (P/'配音计划.json').write_text(json.dumps(plan,ensure_ascii=False,indent=2),encoding='utf8')
    # Rebuild the existing bright music from a complete middle phrase.
    with wave.open(str(P.parent/'重剪_v4/轻快钢琴打击乐_v4.wav'),'rb') as f:
        sr=f.getframerate();phrase=round((60/106)*4*16*sr);f.setpos(phrase)
        music=np.frombuffer(f.readframes(phrase),dtype='<i2').reshape(-1,2)
    music=np.tile(music,(math.ceil(offset*sr/len(music)),1))[:round(offset*sr)].copy()
    music[:sr*2]=(music[:sr*2].astype(np.float32)*np.linspace(0,1,sr*2)[:,None]).astype('<i2')
    music[-sr*3:]=(music[-sr*3:].astype(np.float32)*np.linspace(1,0,sr*3)[:,None]).astype('<i2')
    with wave.open(str(P/'配乐.wav'),'wb') as f:
        f.setnchannels(2);f.setsampwidth(2);f.setframerate(sr);f.writeframes(music.tobytes())
    print(f'prepared {offset}s, {len(captions)} captions',flush=True)
    return timeline

def render_area(preview=False):
    graph=('[0:v]split=2[t][r];[t]crop=2560:150:0:0,scale=1100:64[top];'
           '[r]crop=1510:328:750:245,scale=1100:239[report];'
           'color=c=0x0d1d29:s=1920x1080:r=60:d=12[bg];'
           '[bg][top]overlay=760:285[b1];[b1][report]overlay=760:370,ass=area.ass[v]')
    args=['-loop','1','-framerate','60','-i',str(P.parent/'性能工具截图/01_TD_资源利用率.png'),'-filter_complex',graph,'-map','[v]']
    if preview:v6.run([*args,'-frames:v','1','-q:v','2','area_layout.jpg'],'area_preview')
    elif not (P/'area.mp4').exists():v6.run([*args,'-t','12',*v6.ENC,'area.mp4'],'area')

async def voice():
    sp=importlib.util.spec_from_file_location('tts',OLD/'synthesize_voice.py');tts=importlib.util.module_from_spec(sp);sp.loader.exec_module(tts)
    tts.P=P;tts.OUT=P/'voice';tts.OUT.mkdir(exist_ok=True);tts.RATES['value']=0
    old_records={r['id']:r for r in json.loads((OLD/'配音生成记录.json').read_text(encoding='utf8'))}
    plan=json.loads((P/'配音计划.json').read_text(encoding='utf8'));records=[]
    for s in plan['segments']:
        old=old_records.get(s['id'])
        if old and old['spoken_text']==s['spoken_text']:
            r=dict(old)
        else:r=await tts.generate(s)
        r.update(start=s['start'],end=s['end'])
        records.append(r)
        (P/'配音生成记录.json').write_text(json.dumps(records,ensure_ascii=False,indent=2),encoding='utf8')
        print(f'voice {s["id"]} {r["duration"]:.2f}s',flush=True)
    audio=np.zeros(plan['duration']*48000,np.float32)
    for r in records:
        with wave.open(r['file'],'rb') as f:a=np.frombuffer(f.readframes(f.getnframes()),dtype='<i2').astype(np.float32)/32768
        start=round(r['start']*48000);audio[start:start+len(a)]+=a
    with wave.open(str(P/'旁白_原始对轴.wav'),'wb') as f:
        f.setnchannels(1);f.setsampwidth(2);f.setframerate(48000);f.writeframes((np.clip(audio,-1,1)*32767).astype('<i2').tobytes())

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--prepare',action='store_true');parser.add_argument('--voice',action='store_true');parser.add_argument('--preview',action='store_true');args=parser.parse_args()
    if args.voice:asyncio.run(voice())
    else:
        timeline=prepare()
        if not args.prepare:
            for s in timeline:
                if s['name'] in ['pose','hardware']:v6.render(s,args.preview)
            render_area(args.preview)
