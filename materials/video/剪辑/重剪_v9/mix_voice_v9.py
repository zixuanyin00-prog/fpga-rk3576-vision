from pathlib import Path
import json,re,subprocess,wave
import numpy as np

P=Path(__file__).resolve().parent
FF=str(P.parent.parent/'dataflow_film_20261028/.tools/imageio_ffmpeg/binaries/ffmpeg-win-x86_64-v7.1.exe')
SR=48000
DURATION=json.loads((P/'配音计划.json').read_text(encoding='utf8'))['duration']

def normalize(src,out,target):
    stats=subprocess.run([FF,'-hide_banner','-nostdin','-i',str(src),'-af',f'loudnorm=I={target}:TP=-2:LRA=9:print_format=json','-f','null','-'],capture_output=True,text=True,encoding='utf8',check=True).stderr
    measured=json.loads(re.findall(r'\{\s*"input_i"[\s\S]*?\}',stats)[-1])
    filt=(f'loudnorm=I={target}:TP=-2:LRA=9:measured_I={measured["input_i"]}:measured_TP={measured["input_tp"]}:'
          f'measured_LRA={measured["input_lra"]}:measured_thresh={measured["input_thresh"]}:offset={measured["target_offset"]}:linear=true')
    with (P/(out.stem+'.log')).open('w',encoding='utf8') as log:
        subprocess.run([FF,'-y','-hide_banner','-nostdin','-i',str(src),'-af',filt,'-ar',str(SR),'-c:a','pcm_s16le',str(out)],stdout=log,stderr=log,check=True)
    return measured

def read(file):
    with wave.open(str(file),'rb') as f:
        assert f.getframerate()==SR and f.getsampwidth()==2
        return np.frombuffer(f.readframes(f.getnframes()),dtype='<i2').astype(np.float32).reshape(-1,f.getnchannels())/32768

voice_stats=normalize(P/'旁白_原始对轴.wav',P/'旁白.wav',-18)
music_stats=normalize(P/'配乐.wav',P/'低音量配乐.wav',-36)
voice=read(P/'旁白.wav');music=read(P/'低音量配乐.wav')
assert len(voice)==len(music)==round(DURATION*SR)
mix=voice+music;peak=float(np.max(np.abs(mix)));gain=min(1,10**(-1.5/20)/peak);mix*=gain
with wave.open(str(P/'混音.wav'),'wb') as f:
    f.setnchannels(2);f.setsampwidth(2);f.setframerate(SR);f.writeframes((mix*32767).astype('<i2').tobytes())
out=P/'FPGA视觉系统_实战应用精简版_v9.mp4'
with (P/'final_render.log').open('w',encoding='utf8') as log:
    subprocess.run([FF,'-y','-hide_banner','-nostdin','-i','video_transitions.mp4','-i','混音.wav','-map','0:v:0','-map','1:a:0',
        '-vf','ass=final.ass','-c:v','h264_nvenc','-preset','p5','-cq','19','-b:v','0','-pix_fmt','yuv420p','-r','60','-video_track_timescale','60000',
        '-c:a','aac','-b:a','256k','-t',str(DURATION),'-movflags','+faststart',str(out)],cwd=P,stdout=log,stderr=log,check=True)
records=json.loads((P/'配音生成记录.json').read_text(encoding='utf8'))
report=dict(output=str(out),duration=DURATION,voice='zh-CN-YunyangNeural',voice_target_lufs=-18,music_target_lufs=-36,music_level='constant',speech_clips=len(records),speech_seconds=sum(r['duration'] for r in records),voice_measurement=voice_stats,music_measurement=music_stats,mix_sample_peak_dbfs=round(20*np.log10(peak*gain),2))
(P/'混音记录.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf8')
print(json.dumps(report,ensure_ascii=True),flush=True)
