"""Narrative, cadence and highlight update, preserving the 179.8 second edit."""
from pathlib import Path
import argparse, asyncio, copy, importlib.util, json, re, subprocess, wave
import numpy as np
from PIL import ImageFont

P = Path(__file__).resolve().parent
M = P.parent
OLD = M / '重剪_v13'
FF = M.parent / 'dataflow_film_20261028/.tools/imageio_ffmpeg/binaries/ffmpeg-win-x86_64-v7.1.exe'
OUT = P / 'FPGA视觉系统_亮点与流畅度优化_v15.mp4'
DURATION = 179.8
CHANGES = [
    dict(id=1, scene='intro', start=.55, end=7.25, spoken_text='我们将小样本学习与高速识别结合，构建可快速部署的工件分拣系统。'),
    dict(id=25, scene='console_intro', start=29.55, end=35.25, spoken_text='我们还制作了配套控制台，把采集、训练和部署串成一套流程。'),
    dict(id=24, scene='fewshot_value', start=77.45, end=84.25, spoken_text='本次用十五张原图建立三类姿态模型，可通过控制台快速部署。'),
    dict(id=21, scene='app_execute', start=158.2, end=165.8, spoken_text='实测分拣速度达到每分钟一千二百件，支持连续自动分拣。'),
]


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


async def voice():
    tts = load_module('v15_tts', M / '重剪_v6/synthesize_voice.py')
    tts.P = P
    tts.OUT = P / 'voice'
    tts.OUT.mkdir(exist_ok=True)
    for file in tts.OUT.glob('*.mp3'):
        if file.stat().st_size == 0:
            file.unlink()
    old = json.loads((OLD / '配音生成记录.json').read_text(encoding='utf8'))
    records = [r for r in old if r['id'] not in {s['id'] for s in CHANGES}]
    for cue in CHANGES:
        tts.RATES[cue['scene']] = 0
        request = dict(cue, end=cue['start'] + 15)
        generated = await tts.generate(request)
        assert generated['rate'] == 0
        assert generated['duration'] <= cue['end'] - cue['start'], (cue['id'], generated['duration'])
        generated.update(start=cue['start'], end=cue['end'], remaining_silence=round(cue['end']-cue['start']-generated['duration'],4))
        records.append(generated)
        print(f'Voice {cue["id"]}: {generated["duration"]}s, natural rate 0%', flush=True)
    records.sort(key=lambda r: r['start'])
    for left, right in zip(records, records[1:]):
        assert left['start'] + left['duration'] + .15 <= right['start'], (left['id'], right['id'])
    (P / '配音生成记录.json').write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding='utf8')


def stamp(seconds, srt=False):
    scale = 1000 if srt else 100
    value = round(seconds * scale)
    h, value = divmod(value, 3600 * scale)
    m, value = divmod(value, 60 * scale)
    s, fraction = divmod(value, scale)
    return f'{h:02}:{m:02}:{s:02},{fraction:03}' if srt else f'{h}:{m:02}:{s:02}.{fraction:02}'


def captions():
    old = (M / '重剪_v14/final.ass').read_text(encoding='utf-8-sig')
    lines = old.splitlines()
    header = '\n'.join(line for line in lines if not line.startswith('Dialogue:')) + '\n'
    labels = [line for line in lines if line.startswith('Dialogue:') and line.split(',', 9)[3] == 'Rule']
    # This is a subject title, not an editorial label. Keep it with the opening image.
    labels.append(r'Dialogue: 0,0:00:02.15,0:00:05.80,Title,,0,0,0,,{\fad(150,150)\pos(960,188)\fs58}让工件自动分开')
    font = ImageFont.truetype('C:/Windows/Fonts/msyh.ttc', 44)
    records = json.loads((P / '配音生成记录.json').read_text(encoding='utf8'))
    caps, srt, checks = [], [], []
    dialogues = []
    for r in records:
        words = r['spoken_text']
        for before, after in [('F P G A', 'FPGA'), ('R K', 'RK'), ('三五七六', '3576'),
                              ('四七三', '473'), ('一零零四', '1004'), ('一千二百', '1200')]:
            words = words.replace(before, after)
        text = words
        if font.getlength(text) > 1320:
            candidates = [i + 1 for i, c in enumerate(text) if c in '，；。' and 6 < i < len(text) - 6]
            cut = min(candidates, key=lambda i: abs(font.getlength(text[:i]) - font.getlength(text[i:]))) if candidates else len(text) // 2
            text = text[:cut] + '\n' + text[cut:]
        a = max(0, r['start'] - .12)
        b = min(DURATION - .18, r['start'] + r['duration'] + .18)
        dialogues.append(f'Dialogue: 1,{stamp(a)},{stamp(b)},Default,,0,0,0,,{{\\fad(120,120)}}' + text.replace('\n', r'\N'))
        caps.append(dict(id=r['id'], scene=r['scene'], text=text, startMs=round(a*1000), endMs=round(b*1000)))
        srt.append(f'{len(srt)+1}\n{stamp(a, True)} --> {stamp(b, True)}\n{text}')
        assert re.sub(r'\s+', '', text) == re.sub(r'\s+', '', words)
        checks.append(dict(id=r['id'], spoken_text=r['spoken_text'], subtitle=text, same_words_after_numeric_and_acronym_normalization=True))
    ass = header + '\n'.join(labels + dialogues) + '\n'
    assert not any(word in ass for word in ['引子', '提示词', '同屏呈现', '不表示'])
    (P / 'final.ass').write_text(ass, encoding='utf-8-sig')
    (P / 'captions.json').write_text(json.dumps(caps, ensure_ascii=False, indent=2), encoding='utf8')
    (P / '全片字幕.srt').write_text('\n\n'.join(srt)+'\n', encoding='utf-8-sig')
    (P / '字幕配音逐句核对.json').write_text(json.dumps(checks, ensure_ascii=False, indent=2), encoding='utf8')
    (P / 'chapters.ffmeta').write_text((M / '重剪_v14/chapters.ffmeta').read_text(encoding='utf8'), encoding='utf8')


def audio():
    sr = 48000
    def read(file):
        with wave.open(str(file), 'rb') as f:
            assert f.getframerate() == sr and f.getsampwidth() == 2
            return np.frombuffer(f.readframes(f.getnframes()), dtype='<i2').astype(np.float64).reshape(-1, f.getnchannels()) / 32768
    def write(file, samples):
        with wave.open(str(file), 'wb') as f:
            f.setnchannels(samples.shape[1]); f.setsampwidth(2); f.setframerate(sr)
            f.writeframes(np.rint(np.clip(samples, -1, 1)*32767).astype('<i2').tobytes())
    records = json.loads((P / '配音生成记录.json').read_text(encoding='utf8'))
    speech = np.zeros((round(DURATION*sr), 1), dtype=np.float64)
    for r in records:
        data = read(r['file'])
        start = round(r['start']*sr)
        speech[start:start+len(data)] += data
    raw = P / '旁白_原始对轴.wav'
    write(raw, speech)
    stats = subprocess.run([str(FF), '-hide_banner', '-nostdin', '-i', str(raw), '-af',
                            'loudnorm=I=-18:TP=-2:LRA=9:print_format=json', '-f', 'null', '-'],
                           capture_output=True, text=True, encoding='utf8', check=True).stderr
    v = json.loads(re.findall(r'\{\s*"input_i"[\s\S]*?\}', stats)[-1])
    filt = f'loudnorm=I=-18:TP=-2:LRA=9:measured_I={v["input_i"]}:measured_TP={v["input_tp"]}:measured_LRA={v["input_lra"]}:measured_thresh={v["input_thresh"]}:offset={v["target_offset"]}:linear=true'
    subprocess.run([str(FF), '-v', 'error', '-nostdin', '-y', '-i', str(raw), '-af', filt,
                    '-ar', str(sr), '-c:a', 'pcm_s16le', str(P / '旁白.wav')], check=True)
    music = read(M / '重剪_v12/低音量配乐.wav')
    bed = np.zeros((round(DURATION*sr), 2), dtype=np.float64)
    bed[:2*sr] = music[:2*sr]
    cut = round(75.5*sr)
    bed[2*sr:2*sr+cut] = music[:cut]
    new = music[75*sr:82*sr]
    suffix = music[cut:]
    overlap = sr//2
    w = np.linspace(0, 1, overlap, endpoint=False)[:, None]
    begin, end = 77*sr, 84*sr
    bed[begin:begin+overlap] = bed[begin:begin+overlap]*(1-w) + new[:overlap]*w
    bed[begin+overlap:end-overlap] = new[overlap:-overlap]
    bed[end-overlap:end] = new[-overlap:]*(1-w) + suffix[:overlap]*w
    bed[end:] = suffix[overlap:]
    mix = read(P / '旁白.wav') + bed
    peak = float(np.max(np.abs(mix)))
    gain = min(1, 10**(-1.8/20)/peak)
    write(P / '混音.wav', mix*gain)
    (P / '混音记录.json').write_text(json.dumps(dict(voice_target_lufs=-18, music_target_lufs=-36,
        music_level='constant', speech_clips=len(records), voice_measurement=v,
        mix_sample_peak_dbfs=round(20*np.log10(peak*gain),2)), ensure_ascii=False, indent=2), encoding='utf8')


def video():
    tr = load_module('v15_transitions', M / '重剪_v12/add_transitions.py')
    tr.P = P
    old = json.loads((M / '重剪_v12/timeline.json').read_text(encoding='utf8'))
    replacements = {'end': P/'end.mp4', 'fewshot_value': P/'fewshot_value.mp4'}
    cadence = json.loads((P/'cadence/replacements.json').read_text(encoding='utf8'))
    replacements.update({name: Path(file) for name, file in cadence.items()})
    sources = json.loads((P/'cadence/source_overrides.json').read_text(encoding='utf8'))
    timeline = copy.deepcopy(json.loads((OLD / 'timeline.json').read_text(encoding='utf8')))
    for s in timeline:
        if s['name'] in replacements:
            s['file'] = str(replacements[s['name']])
        if s['name'] in sources:
            s['source'] = sources[s['name']]
    (P/'timeline.json').write_text(json.dumps(timeline, ensure_ascii=False, indent=2), encoding='utf8')
    parts = P / 'transition_parts'
    parts.mkdir(exist_ok=True)
    def render(name, inputs, graph, frames):
        file = parts / name
        args = []
        for source in inputs:
            args += ['-i', str(source)]
        signature = dict(sources=[dict(file=str(source),size=source.stat().st_size,mtime_ns=source.stat().st_mtime_ns) for source in inputs], graph=graph, frames=frames)
        tr.render(file, args, graph, frames, signature, parts)
        return file
    paths = []
    for line in (OLD/'transition_parts/concat.txt').read_text(encoding='utf8').splitlines():
        assert line.startswith("file '") and line.endswith("'")
        paths.append(Path(line[6:-1]))
    for i, file in enumerate(paths):
        match = re.fullmatch(r'body_(\d+)\.mp4', file.name)
        if match:
            index = int(match[1]); scene = old[index]
            if scene['name'] not in replacements:
                continue
            frames = round(scene['duration']*60)
            a = 12 if index else 0
            b = frames-(12 if index<len(old)-1 else 0)
            paths[i] = render(file.name, [replacements[scene['name']]], f'[0:v]trim=start_frame={a}:end_frame={b},setpts=PTS-STARTPTS,setsar=1,fps=60[v]', b-a)
        match = re.fullmatch(r'bridge_(\d+)\.mp4', file.name)
        if match:
            index = int(match[1]); left, right = old[index:index+2]
            if not {left['name'],right['name']} & replacements.keys():
                continue
            inputs = [replacements.get(s['name'], Path(s['file'])) for s in [left,right]]
            paths[i] = render(file.name, inputs, tr.bridge_graph(round(left['duration']*60)), 24)
    # The few-shot insert uses true 0.5s overlaps and different body lengths.
    run = next(s for s in old if s['name']=='console_run')
    logic = next(s for s in old if s['name']=='chapter_logic')
    left = replacements.get('console_run', Path(run['file']))
    right = Path(logic['file']); addition = replacements['fewshot_value']
    for i,file in enumerate(paths):
        if file.name=='body_run.mp4':
            paths[i]=render(file.name,[left],'[0:v]trim=start_frame=12:end_frame=390,setpts=PTS-STARTPTS,setsar=1,fps=60[v]',378)
        elif file.name=='body_fewshot.mp4':
            paths[i]=render(file.name,[addition],'[0:v]trim=start_frame=30:end_frame=390,setpts=PTS-STARTPTS,setsar=1,fps=60[v]',360)
        elif file.name in ['bridge_into_fewshot.mp4','bridge_out_of_fewshot.mp4']:
            entering=file.name=='bridge_into_fewshot.mp4'
            l,r,a,b=(left,addition,390,420) if entering else (addition,right,390,420)
            graph=(f'[0:v]trim=start_frame={a}:end_frame={b},setpts=N/(60*TB),setsar=1,fps=60[a];'
                   '[1:v]trim=start_frame=0:end_frame=30,setpts=N/(60*TB),setsar=1,fps=60[b];'
                   "[a][b]blend=all_expr='A*(1-T/0.5)+B*(T/0.5)':shortest=1,trim=end_frame=30,setpts=N/(60*TB)[v]")
            paths[i]=render(file.name,[l,r],graph,30)
    concat=parts/'concat.txt'
    concat.write_text(''.join("file '"+file.as_posix()+"'\n" for file in paths),encoding='utf8')
    with (parts/'assemble.log').open('w',encoding='utf8') as log:
        subprocess.run([str(FF),'-y','-hide_banner','-nostdin','-f','concat','-safe','0','-i',str(concat),'-map','0:v:0','-c:v','copy','-an','-movflags','+faststart',str(P/'video_clean.mp4')],stdout=log,stderr=log,check=True)
    assert tr.probe(P/'video_clean.mp4',10788)
    print('179.8s clean video assembled',flush=True)


def final():
    with (P / 'final_render.log').open('w',encoding='utf8') as log:
        subprocess.run([str(FF),'-y','-hide_banner','-nostdin','-i','video_clean.mp4','-i','混音.wav','-i','chapters.ffmeta',
            '-map','0:v:0','-map','1:a:0','-map_metadata','2','-map_chapters','2','-vf','ass=final.ass',
            '-c:v','h264_nvenc','-preset','p5','-cq','19','-b:v','0','-pix_fmt','yuv420p','-r','60','-video_track_timescale','60000',
            '-c:a','aac','-b:a','256k','-t',str(DURATION),'-movflags','+faststart',str(OUT)],cwd=P,stdout=log,stderr=log,check=True)
    print('COMPLETE '+str(OUT),flush=True)


if __name__ == '__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('stage',choices=['voice','compose','video','final'])
    args=parser.parse_args()
    if args.stage=='voice':asyncio.run(voice())
    elif args.stage=='compose':captions();audio()
    elif args.stage=='video':video()
    else:final()
