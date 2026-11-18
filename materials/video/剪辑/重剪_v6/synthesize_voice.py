from pathlib import Path
import argparse, asyncio, hashlib, json, math, subprocess, sys, wave
import numpy as np

P=Path(__file__).resolve().parent
sys.path.insert(0,str(P/'.voice_deps'))
import edge_tts

FF=str(P.parent.parent/'dataflow_film_20261028/.tools/imageio_ffmpeg/binaries/ffmpeg-win-x86_64-v7.1.exe')
VOICE='zh-CN-YunyangNeural'
SR=48000
OUT=P/'voice'
OUT.mkdir(exist_ok=True)
RATES={'pose':0,'board':4,'new_run':2,'new_pan':3,'parameter':0,'isp_overview':0,'isp_processing':2,'capture':4,'candidates':5,'confirm':0,'train':2,'coarsefine':2,'test':3,'deploy':2,'async':0,'tracking':0,'hardware':0,'area':2,'timing':2,'collage':0,'end':-5}

def load_plan():
    plan=json.loads((P/'voice_plan.json').read_text(encoding='utf8'))
    scenes=json.loads((P/'scenes.json').read_text(encoding='utf8'))['scenes']
    lookup={}
    offset=0
    for s in scenes:
        for i,c in enumerate(s['captions'],1):lookup[(s['name'],i)]=(offset+c['start'],offset+c['end'],c['text'])
        offset+=s['duration']
    for s in plan['segments']:
        a,b,t=lookup[(s['scene'],s['scene_caption_index'])]
        s.update(caption_start=a,caption_end=b,start=a+.2,end=b-.2,text=t)
        if s['scene']=='isp_processing' and s['scene_caption_index']==4:
            s['spoken_text']='彩色边缘叠加，突出工件轮廓。'
        elif s['scene']=='isp_processing' and s['scene_caption_index']==5:
            s['spoken_text']='效果确认后，固定训练采图参数，让同一批样本保持一致的成像条件。'
        elif s['scene']=='deploy' and s['scene_caption_index']==1:
            s['spoken_text']='训练模型用于设备端识别，运行入口同时集成 H D M I 监控和参数操作。'
    plan['segments'].append(dict(id=49,scene='end',scene_caption_index=1,caption_start=304.5,caption_end=309.5,start=304.9,end=309.1,text='从看见工件，到确认姿态。',spoken_text='从看见工件，到确认姿态。'))
    return plan

def decode(file):
    b=subprocess.check_output([FF,'-v','error','-i',str(file),'-ac','1','-ar',str(SR),'-f','f32le','-'])
    a=np.frombuffer(b,dtype='<f4').copy()
    # Trim only exterior service padding, keeping consonants and natural ends.
    n=len(a)//480
    levels=np.sqrt((a[:n*480].reshape(n,480)**2).mean(axis=1))
    active=np.flatnonzero(levels>.002)
    if not len(active):raise ValueError('Empty speech '+str(file))
    return a[max(0,int(active[0]*480)-2880):min(len(a),int((active[-1]+1)*480)+7200)]

async def generate(s):
    available=s['end']-s['start'];rate=RATES[s['scene']]
    key=hashlib.sha256((VOICE+s['spoken_text']+str(available)+str(rate)).encode()).hexdigest()[:12]
    base=f'{s["id"]:02}_{key}'
    record=OUT/(base+'.json')
    if record.exists():return json.loads(record.read_text(encoding='utf8'))
    for attempt in range(3):
        mp3=OUT/(base+f'_{rate:+d}.mp3')
        if not mp3.exists():
            await edge_tts.Communicate(s['spoken_text'],VOICE,rate=f'{rate:+d}%',pitch='-1Hz',connect_timeout=15,receive_timeout=45).save(str(mp3))
        data=decode(mp3)
        seconds=len(data)/SR
        if seconds<=available+.025:break
        rate=math.ceil((1+rate/100)*(seconds/available)*100-100)+1
        if rate>22:raise ValueError(f'Voice cue {s["id"]} needs shorter wording: {seconds:.2f}s / {available:.2f}s at {rate}%')
    if seconds>available+.025:raise ValueError('Voice cue overflow '+str(s['id']))
    # Soft edges prevent edit clicks; the speech prosody itself remains intact.
    edge=240
    data[:edge]*=np.linspace(0,1,edge);data[-edge:]*=np.linspace(1,0,edge)
    wav=OUT/(base+'.wav')
    with wave.open(str(wav),'wb') as f:
        f.setnchannels(1);f.setsampwidth(2);f.setframerate(SR);f.writeframes((np.clip(data,-1,1)*32767).astype('<i2').tobytes())
    result=dict(id=s['id'],scene=s['scene'],spoken_text=s['spoken_text'],start=s['start'],end=s['end'],duration=round(seconds,4),rate=rate,voice=VOICE,file=str(wav),remaining_silence=round(available-seconds,4))
    record.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf8')
    return result

async def main():
    parser=argparse.ArgumentParser();parser.add_argument('--probe',action='store_true');args=parser.parse_args()
    plan=load_plan()
    (P/'配音计划_最终.json').write_text(json.dumps(plan,ensure_ascii=False,indent=2),encoding='utf8')
    segments=[s for s in plan['segments'] if not args.probe or s['id'] in [1,7,41]]
    report=[]
    for s in segments:
        r=await generate(s);report.append(r)
        print(json.dumps({k:r[k] for k in ['id','scene','duration','rate','remaining_silence']}),flush=True)
        (P/'配音生成记录.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf8')
    if args.probe:return
    all_audio=np.zeros(round(310*SR),np.float32)
    for r in report:
        with wave.open(r['file'],'rb') as f:a=np.frombuffer(f.readframes(f.getnframes()),dtype='<i2').astype(np.float32)/32768
        start=round(r['start']*SR)
        all_audio[start:start+len(a)]+=a
    with wave.open(str(P/'旁白_原始对轴.wav'),'wb') as f:
        f.setnchannels(1);f.setsampwidth(2);f.setframerate(SR);f.writeframes((np.clip(all_audio,-1,1)*32767).astype('<i2').tobytes())
    print('VOICE COMPLETE',flush=True)

if __name__=='__main__':asyncio.run(main())
