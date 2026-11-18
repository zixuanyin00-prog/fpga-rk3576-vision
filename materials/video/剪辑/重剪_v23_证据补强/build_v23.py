"""Evidence-led revision of the selected B film; retain the 179.8 second limit."""
from pathlib import Path
import argparse,asyncio,copy,importlib.util,json,re,subprocess,sys,concurrent.futures,wave
import numpy as np
from PIL import ImageFont

P=Path(__file__).resolve().parent;M=P.parent;OLD=M/'重剪_v21'
OUT=P/'FPGA视觉系统_B实证补强_v23.mp4';DURATION=179.8
FF=M.parent/'dataflow_film_20261028/.tools/imageio_ffmpeg/binaries/ffmpeg-win-x86_64-v7.1.exe'
EVIDENCE={'isp_methods':7,'compression_pcie':6.4,'tracking_short':4.2,'dimensions':5.6,'alerts':6.5}


def load(name,file):
    spec=importlib.util.spec_from_file_location(name,file);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);return m


base=load('v23_old',M/'重剪_v19/build_v19.py')
tr=load('v23_transitions',M/'重剪_v12/add_transitions.py');tr.P=P
mixbase=base.b;mixbase.P=P;mixbase.OUT=OUT


def normalize(text):
    return base.normalize(text).replace('百分之九十二点八七','92.87%')


def prepare():
    original=json.loads((OLD/'timeline.json').read_text('utf8'));rows=[]
    for item in original:
        s=copy.deepcopy(item)
        if s['name'] in EVIDENCE:
            s['duration']=EVIDENCE[s['name']];s['file']=str(P/'scenes'/(s['name']+'.mp4'));s['source']={'renderer':'evidence_scenes.cjs','basis':'local source images and records'}
        if s['name']=='pose_proof':
            s['duration']=6;s['file']=str(P/'scenes/pose_proof.mp4');s['source']={'file':item['file'],'start':0,'length':6,'playback_speed':1,'filters':['null']}
            rows.append(s)
            rows.append(dict(name='pose_bowl_lead',duration=4,label='姿态分拣　剔除白色反面电阻',speed_label='1.6×',file=str(P/'scenes/pose_bowl_lead.mp4'),source=dict(file=str(M/'高速整体演示（振动盘） (0-59s可用).MOV'),start=30,length=6.4,playback_speed=1.6,crop='crop=1200:675:650:1330')))
            continue
        if s['name'] in ['pose_burst_a','pose_burst_b']:
            s['file']=str(P/'scenes'/(s['name']+'.mp4'));s['speed_label']='1.5×'
            s['source']=dict(file=str(M/'高速整体演示（振动盘） (0-59s可用).MOV'),start=40 if s['name']=='pose_burst_a' else 47,length=s['duration']*1.5,playback_speed=1.5,crop='crop=1200:675:650:1330')
        if s['name']=='pose_display':
            s['duration']=2.5;s['file']=str(P/'scenes/pose_display.mp4');s['source']['length']=3.25
        if s['name']=='pose_slow':
            s['duration']=12.5;s['file']=str(P/'scenes/pose_slow.mp4');s['speed_label']='0.8× 慢放'
            s['label']='姿态检测　连续画面 · 识别框更新'
            s['source']=dict(file=str(M/'高速整体演示（HDMI）.MOV'),start=25,length=10,playback_speed=.8,crop='crop=1600:900:900:850',interpolate=True)
        if s['name']=='pose_inference':
            s['duration']=5.5;s['file']=str(P/'scenes/pose_inference.mp4');s['source']['length']=5.5*1.4;s['label']='多目标并行识别　分拣功能未开启'
        if s['name']=='category_action':
            s['duration']-=4;s['file']=str(P/'scenes/category_action.mp4')
            s['source']=dict(file=item['file'],segments=[dict(start=0,length=830/60),dict(start=1070/60,length=4.5)],playback_speed=1,original_speed=1.07)
        rows.append(s)
    cursor=0
    for s in rows:s['start']=cursor/60;cursor+=round(s['duration']*60)
    assert cursor==10788,cursor
    assert next(s['start'] for s in rows if s['name']=='pose_bowl_lead')==104.5
    assert next(s['start'] for s in rows if s['name']=='pose_inference')==129
    assert next(s['start'] for s in rows if s['name']=='category_collect')==168.5
    (P/'timeline.json').write_text(json.dumps(rows,ensure_ascii=False,indent=2),'utf8')
    (P/'evidence_render_plan.json').write_text(json.dumps([s for s in rows if s['name'] in EVIDENCE],ensure_ascii=False,indent=2),'utf8')
    old_plan=json.loads((M/'重剪_v19/edit_plan.json').read_text('utf8'));old_rows={s['name']:s for s in original};new_rows={s['name']:s for s in rows}
    voice=[]
    for v in old_plan['voices']:
        v=copy.deepcopy(v)
        if v['scene']=='console_intro':pass
        elif v['scene'] in old_rows:
            offset=new_rows[v['scene']]['start']-old_rows[v['scene']]['start'];v['start']+=offset;v['end']+=offset
        voice.append(v)
    by_id={v['id']:v for v in voice}
    updates={
      7:(39.65,46.3,'同一张图像，对照亮度映射、前景分割与边缘提取的效果。'),
      11:(70.1,76.1,'有损压缩，体积减少百分之九十二点八七；无损解码逐字节一致。'),
      12:(76.45,80.35,'三张真实采集帧，对应同一轨迹编号。'),
      13:(80.65,83.6,None),
      26:(83.85,89.15,'实际输出四个目标框，按原生坐标计算像素宽高。'),
      27:(89.5,95.6,'异常原图回传电脑归档，并支持可选 S D 卡留存。'),
      14:(98.8,103.0,None),30:(104.7,108.4,None),17:(108.6,113.7,None),
      31:(114.1,120.5,None),
      28:(121.4,128.6,'慢放观察连续画面，识别框随工件位置更新。'),
      29:(129.15,134.3,'多目标并行识别，当前演示关闭分拣输出。'),
      22:(168.95,174.4,None),23:(175.05,179.75,None)
    }
    for i,(a,z,words) in updates.items():
        by_id[i].update(start=a,end=z)
        if words:by_id[i]['spoken_text']=words;by_id[i].pop('reuse_id',None)
    voice.sort(key=lambda r:r['start'])
    (P/'voice_plan.json').write_text(json.dumps(voice,ensure_ascii=False,indent=2),'utf8')
    chapters=[('项目介绍',0),('01 板卡介绍',6),('02 控制台与ISP',30.5),('03 识别与工业检测',new_rows['chapter_logic']['start']),('04 实战：姿态检测',new_rows['application']['start']),('类别检测与连续分拣',new_rows['category_material']['start']),('系统亮点',174.8)]
    text=';FFMETADATA1\n'
    for i,(name,a) in enumerate(chapters):
        z=chapters[i+1][1] if i+1<len(chapters) else DURATION
        text+=f'[CHAPTER]\nTIMEBASE=1/1000\nSTART={round(a*1000)}\nEND={round(z*1000)}\ntitle={name}\n'
    (P/'chapters.ffmeta').write_text(text,'utf8')
    (P/'edit_changes.json').write_text(json.dumps(dict(total_seconds=179.8,evidence_extra_seconds=4,removed_original_movie_range=[160,164],hdmi_slow_source=[25,35],hdmi_slow_speed=.8,sorting_disabled_movie_range=[129,134.5],airflow_note_range=[164,168.4],application_start=98.5),ensure_ascii=False,indent=2),'utf8')
    print('Prepared 179.8 seconds; evidence +4s; old 160–164s removed',flush=True)


def render_scene(s):
    file=Path(s['file'])
    if file.parent!=P/'scenes' or s['name'] in EVIDENCE:return
    file.parent.mkdir(exist_ok=True);src=s['source'];frames=round(s['duration']*60)
    if tr.probe(file,frames):print('reuse '+s['name'],flush=True);return
    args=[str(FF),'-y','-hide_banner','-nostdin']
    if src.get('segments'):
        args+=['-i',src['file']];graph=[];labels=[]
        for i,r in enumerate(src['segments']):
            a=round(r['start']*60);z=round((r['start']+r['length'])*60);graph.append(f'[0:v]trim=start_frame={a}:end_frame={z},setpts=PTS-STARTPTS[v{i}]');labels.append(f'[v{i}]')
        graph.append(''.join(labels)+f'concat=n={len(labels)}:v=1:a=0,setsar=1,fps=60[v]');args+=['-filter_complex',';'.join(graph),'-map','[v]']
    else:
        speed=src.get('playback_speed',1);args+=['-ss',str(src.get('start',0)),'-t',str(src['length']+.04),'-i',src['file']]
        filters=[f'setpts=(PTS-STARTPTS)/{speed}']
        if src.get('crop'):filters.append(src['crop'])
        filters+=src.get('filters',['scale=1920:1080:flags=lanczos','drawbox=x=0:y=930:w=iw:h=150:color=0x0d1d29@0.65:t=fill'])
        if src.get('interpolate'):
            # Native capture is ~120fps. Interpolate the slowed display recording at 720p,
            # then upscale; this affects the film only, never model inputs or metrics.
            filters=['setpts=(PTS-STARTPTS)/0.8',src['crop'],'scale=1280:720:flags=lanczos','minterpolate=fps=60:mi_mode=mci:mc_mode=obmc:me_mode=bidir:vsbmc=1','scale=1920:1080:flags=lanczos','drawbox=x=0:y=930:w=iw:h=150:color=0x0d1d29@0.65:t=fill','tpad=stop_mode=clone:stop=3']
        filters+=['setsar=1','fps=60'];args+=['-vf',','.join(filters)]
    with file.with_suffix('.log').open('w',encoding='utf8') as log:subprocess.run([*args,'-frames:v',str(frames),*tr.ENC,str(file)],stdout=log,stderr=log,check=True)
    assert tr.probe(file,frames),s['name']
    print('rendered '+s['name'],flush=True)


def footage():
    rows=json.loads((P/'timeline.json').read_text('utf8'))
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:list(pool.map(render_scene,rows))


async def voice():
    tts=load('v23_tts',M/'重剪_v6/synthesize_voice.py');tts.OUT=P/'voice';tts.OUT.mkdir(exist_ok=True)
    old={r['id']:r for r in json.loads((OLD/'配音生成记录.json').read_text('utf8'))};records=[]
    for cue in json.loads((P/'voice_plan.json').read_text('utf8')):
        prev=old[cue['id']]
        if cue['spoken_text']==prev['spoken_text']:r=copy.deepcopy(prev)
        else:
            tts.RATES[cue['scene']]=0;r=await tts.generate(dict(cue,end=cue['start']+18))
        assert r['duration']<=cue['end']-cue['start']+.001,(cue['id'],r['duration'],cue['end']-cue['start'])
        r.update(id=cue['id'],scene=cue['scene'],start=cue['start'],end=cue['end']);records.append(r)
        print('voice '+str(cue['id'])+' '+str(r['duration']),flush=True)
    for a,z in zip(records,records[1:]):assert a['start']+a['duration']+.12<=z['start'],(a['id'],z['id'])
    (P/'配音生成记录.json').write_text(json.dumps(records,ensure_ascii=False,indent=2),'utf8')


def captions():
    original=(M/'重剪_v19/final.ass').read_text('utf-8-sig');header='\n'.join(s for s in original.splitlines() if not s.startswith('Dialogue:'))
    header=header.replace('[Events]','Style: BSubtitle,Microsoft YaHei,44,&H00473718,&H00473718,&H00E9DDCB,&H00000000,0,0,0,0,100,100,0.5,0,1,0,0,2,100,100,44,1\n[Events]')
    lines=[header];rows=json.loads((P/'timeline.json').read_text('utf8'));oldrows=json.loads((M/'重剪_v19/timeline.json').read_text('utf8'));font=ImageFont.truetype('C:/Windows/Fonts/msyh.ttc',44)
    names=set(EVIDENCE)|{'opening_title','chapter_board','chapter_console','chapter_logic','fewshot_value','trigger_short','application','end'}
    light=[(s['start'],s['start']+s['duration']) for s in rows if s['name'] in names]
    caps=[];srt=[]
    for r in json.loads((P/'配音生成记录.json').read_text('utf8')):
        words=normalize(r['spoken_text']);text=words
        if font.getlength(text)>1320:
            cuts=[i+1 for i,c in enumerate(text) if c in '，；：。' and 4<i<len(text)-4];i=min(cuts,key=lambda i:max(font.getlength(text[:i]),font.getlength(text[i:]))) if cuts else len(text)//2;text=text[:i]+'\n'+text[i:]
        a=max(0,r['start']-.1);z=min(179.7,r['start']+r['duration']+.15)
        caps.append(dict(id=r['id'],scene=r['scene'],text=text,startMs=round(a*1000),endMs=round(z*1000)));srt.append(f'{len(srt)+1}\n{base.b.stamp(a,True)} --> {base.b.stamp(z,True)}\n{text}')
        points=sorted({a,z,*[q for span in light for q in span if a<q<z]})
        for lo,hi in zip(points,points[1:]):
            style='BSubtitle' if any(x<=(lo+hi)/2<y for x,y in light) else 'Default';body=text.replace('\n',r'\N')
            lines.append(f'Dialogue: 1,{base.b.stamp(lo)},{base.b.stamp(hi)},{style},,0,0,0,,{{\\fad({100 if lo==a else 0},{100 if hi==z else 0})}}'+body)
        assert re.sub(r'\s','',text)==re.sub(r'\s','',words)
    # Existing arrows over unchanged hardware and console recordings remain tied to their shots.
    for line in original.splitlines():
        if not line.startswith('Dialogue:'):continue
        f=line.split(',',9);layer=int(f[0].split(':')[1]);a,z=[sum(float(q)*m for q,m in zip(t.split(':'),[3600,60,1])) for t in f[1:3]]
        if layer<2 or z>62.2:continue
        old=next((s for s in oldrows if s['start']<=a and z<=s['start']+s['duration']+.001),None)
        if old is None:continue
        new=next(s for s in rows if s['name']==old['name']);offset=new['start']-old['start'];f[1]=base.b.stamp(a+offset);f[2]=base.b.stamp(z+offset);lines.append(','.join(f))
    labels={'rk_board':'RK3576｜识别与显示','fpga_board':'安路 PH1A90｜图像采集与预处理','control_board':'自制电磁阀转接板｜脉冲控制'}
    for s in rows:
        label=labels.get(s['name'],s.get('label'))
        if label:lines.append(f'Dialogue: 0,{base.b.stamp(s["start"]+.15)},{base.b.stamp(s["start"]+s["duration"]-.15)},Rule,,0,0,0,,{{\\fad(120,120)}}{label}')
        if s.get('speed_label'):lines.append(f'Dialogue: 3,{base.b.stamp(s["start"]+.3)},{base.b.stamp(s["start"]+s["duration"]-.3)},Default,,0,0,0,,{{\\an3\\pos(1840,930)\\fs26\\bord1.3\\shad0\\fad(120,120)}}{s["speed_label"]}')
    def badge(a,z,text,width=1000,y=54,size=31):
        x=960-width/2
        lines.append(f'Dialogue: 3,{base.b.stamp(a)},{base.b.stamp(z)},Default,,0,0,0,,{{\\an7\\pos({x},{y})\\p1\\bord0\\shad0\\1c&H291D0D&\\1a&H18&\\fad(150,150)}}m 0 0 l {width} 0 {width} 78 0 78')
        lines.append(f'Dialogue: 4,{base.b.stamp(a)},{base.b.stamp(z)},Default,,0,0,0,,{{\\an8\\pos(960,{y+15})\\fs{size}\\bord0\\shad0\\1c&HF3F5ED&\\fad(150,150)}}{text}')
    badge(114.3,120.4,'1080p · 60 FPS　｜　采集 → HDMI 显示 < 50 ms',1100,size=31)
    badge(121.4,128.85,'A 数字正向　　B 数字倒向　　C 白色反面',1000,size=30)
    badge(129.1,134.4,'分拣功能未开启',660,size=35)
    badge(164,168.4,'实验室空压机气流过大，偶有正确电阻被误剔除。',1250,size=31)
    ass='\n'.join(lines)+'\n';assert not any(w in ass for w in ['提示词','引子','不表示','零漏检','完美筛选'])
    (P/'final.ass').write_text(ass,'utf-8-sig');(P/'captions.json').write_text(json.dumps(caps,ensure_ascii=False,indent=2),'utf8');(P/'全片字幕.srt').write_text('\n\n'.join(srt)+'\n','utf-8-sig')
    print('captions match spoken words',flush=True)


def compose():
    captions();mixbase.audio()
    synth=load('v23_material_sound',M/'视觉风格小样_v20/make_sound.py');sr=48000
    with wave.open(str(P/'混音.wav'),'rb') as w:
        audio=np.frombuffer(w.readframes(w.getnframes()),dtype='<i2').astype(np.float64).reshape(-1,2)/32768
    fx=np.zeros_like(audio);events=[]
    def add(at,hz,db=-32,pan=0):
        x=synth.bell(hz,.25);x=x/max(.001,np.max(np.abs(x)))*10**(db/20);a=round(at*sr);n=min(len(x),len(fx)-a)
        fx[a:a+n]+=x[:n,None]*np.array([np.sqrt((1-pan)/2),np.sqrt((1+pan)/2)]);events.append(dict(time=at,hz=hz,peak_dbfs=db))
    rows=json.loads((P/'timeline.json').read_text('utf8'))
    for s in rows:
        if s['name']=='tracking_short':
            for i in range(3):add(s['start']+.12+i*.12,660+i*80,-34,(i-1)*.2)
        elif s['name'] in set(EVIDENCE)|{'opening_title','chapter_board','chapter_console','chapter_logic','application','fewshot_value','trigger_short','end'}:
            add(s['start']+.12,520 if s['name'].startswith('chapter') or s['name']=='application' else 660)
    mixed=audio+fx;gain=min(1,10**(-1.8/20)/np.max(np.abs(mixed)))
    for name,data in [('证据页音效.wav',fx),('混音.wav',mixed*gain)]:
        with wave.open(str(P/name),'wb') as w:w.setnchannels(2);w.setsampwidth(2);w.setframerate(sr);w.writeframes(np.rint(np.clip(data,-1,1)*32767).astype('<i2').tobytes())
    (P/'音效对轴.json').write_text(json.dumps(dict(events=events,music_level='constant low bed',voice_and_music_source='retimed narration and same background music',gain=gain),ensure_ascii=False,indent=2),'utf8')


def assemble():
    argv=sys.argv;sys.argv=['add_transitions.py','--timeline',str(P/'timeline.json')];tr.main();sys.argv=argv;(P/'video_transitions.mp4').replace(P/'video_clean.mp4')


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('stage',choices=['prepare','footage','voice','compose','assemble','final']);a=parser.parse_args()
    if a.stage=='voice':asyncio.run(voice())
    elif a.stage=='final':mixbase.final()
    else:globals()[a.stage]()
