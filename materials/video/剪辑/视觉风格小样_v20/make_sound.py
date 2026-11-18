"""Three deterministic, original sonic identities for the procedural previews."""
from pathlib import Path
import json,subprocess,wave
import numpy as np

P=Path(__file__).resolve().parent
FF=P.parent.parent/'dataflow_film_20261028/.tools/imageio_ffmpeg/binaries/ffmpeg-win-x86_64-v7.1.exe'
SR=48000
rng=np.random.default_rng(20261103)


def noise(seconds,lo,hi):
    n=round(seconds*SR);x=rng.normal(size=n);f=np.fft.rfftfreq(n,1/SR)
    w=np.clip((f-lo)/max(40,lo*.3),0,1)*np.clip((hi-f)/max(80,hi*.2),0,1)
    y=np.fft.irfft(np.fft.rfft(x)*w,n=n)
    return y/max(.001,np.std(y))


def bell(freq,seconds=.35):
    t=np.arange(round(seconds*SR))/SR
    x=sum(level*np.sin(2*np.pi*freq*ratio*t)*np.exp(-t*decay) for ratio,level,decay in [(1,.65,10),(2.76,.22,19),(5.4,.09,34)])
    return x*np.minimum(1,t/.006)


def wood(seconds=.12,frequency=190):
    t=np.arange(round(seconds*SR))/SR
    click=noise(seconds,1700,7200)*np.exp(-t*95)*.27
    tone=(np.sin(2*np.pi*frequency*t)+.28*np.sin(2*np.pi*frequency*2.31*t))*np.exp(-t*42)
    return (tone*.6+click)*np.minimum(1,t/.001)


def beep(freq,seconds=.09):
    t=np.arange(round(seconds*SR))/SR
    x=sum(np.sin(2*np.pi*freq*k*t)/k for k in [1,3,5])*.6
    fade=np.minimum(1,t/.002)*np.minimum(1,(seconds-t)/.006)
    return x*fade


def write(file,samples):
    with wave.open(str(file),'wb') as w:
        w.setnchannels(2);w.setsampwidth(2);w.setframerate(SR)
        w.writeframes(np.rint(np.clip(samples,-1,1)*32767).astype('<i2').tobytes())


def create(style):
    meta=json.loads((P/f'style_{style}_manifest.json').read_text(encoding='utf8'))
    mix=np.zeros((16*SR,2));count=0
    def add(at,x,gain=.25,pan=0):
        nonlocal count
        start=round(at*SR);n=min(len(x),len(mix)-start)
        balance=np.array([np.sqrt((1-pan)/2),np.sqrt((1+pan)/2)])
        mix[start:start+n]+=x[:n,None]*gain*balance;count+=1
    for event in meta['sound_events']:
        at=event['time'];kind=event['type'];observation=abs(at-5.2)<.01 or abs(at-7.4)<.01 or abs(at-9.6)<.01
        pan=-.45 if abs(at-5.2)<.01 else .45 if abs(at-9.6)<.01 else 0
        if style=='a':
            if 'whoosh' in kind:
                seconds=.58;t=np.arange(round(seconds*SR))/SR
                add(at,noise(seconds,750,4800)*np.sin(np.pi*t/seconds)**2,.12,-.2)
                add(at+.51,wood(.08,260),.25,.25)
            elif observation:add(at,wood(.09,260+int((at-5.2)/2.2)*95),.36,pan)
            elif 'confirm' in kind:
                for dt,f in [(0,230),(.065,345),(.13,460)]:add(at+dt,wood(.1,f),.28)
            else:add(at,wood(.13,165 if at<.3 else 300),.4)
        elif style=='b':
            if 'fold' in kind:
                seconds=.75;t=np.arange(round(seconds*SR))/SR
                add(at,noise(seconds,1500,5600)*np.sin(np.pi*t/seconds)**2,.065,-.1)
                add(at+.64,bell(520,.3),.17,.2)
            elif observation:add(at,bell(event.get('pitch',720),.4),.38,pan)
            elif 'settle' in kind:
                add(at,bell(440,.58)+bell(660,.58)*.32,.31)
                add(at+.04,wood(.13,210),.11)
            else:
                add(at,bell(390,.25),.24)
                add(at,wood(.08,165),.048)
        else:
            if 'double_beep' in kind:
                add(at,beep(820,.085),.25,-.12);add(at+.14,beep(1095,.115),.22,.12)
            elif 'beep' in kind:add(at,beep(1440,.07),.18)
            elif 'shutter' in kind:
                add(at,wood(.055,310),.3)
                seconds=.13;t=np.arange(round(seconds*SR))/SR
                add(at,noise(seconds,800,4600)*np.exp(-t*28),.095)
            elif 'paper' in kind:
                for j in range(5):add(at+j*.035,wood(.025,650+j*70),.14,(-1)**j*.2)
            elif observation:
                add(at,wood(.045,420),.27,pan);add(at+.025,beep(1100+int((at-5.2)/2.2)*170,.05),.10,pan)
            else:add(at,wood(.055,350),.32)
    peak=float(np.max(np.abs(mix)));mix*=10**(-6/20)/peak
    wav=P/f'style_{style}_sound.wav';write(wav,mix)
    return dict(style=style,events=len(meta['sound_events']),layers=count,sample_rate=SR,peak_dbfs=-6,original_procedural_synthesis=True,reference_audio_used=False,voice=False)


if __name__=='__main__':
    report=[create(s) for s in 'abc']
    (P/'sound_design.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf8')
    print(json.dumps(report),flush=True)
