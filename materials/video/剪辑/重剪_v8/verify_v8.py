from pathlib import Path
import io, json, re, shutil, subprocess
from PIL import Image, ImageDraw, ImageFont

P = Path(__file__).resolve().parent
FF = P.parent.parent / 'dataflow_film_20261028/.tools/imageio_ffmpeg/binaries/ffmpeg-win-x86_64-v7.1.exe'
OUT = P / 'FPGA视觉系统_自动画框与字幕统一_v8.mp4'
records = json.loads((P/'配音生成记录.json').read_text(encoding='utf8'))
caps = json.loads((P/'captions.json').read_text(encoding='utf8'))
timeline = json.loads((P/'timeline.json').read_text(encoding='utf8'))
by_id = {c['id']: c for c in caps}
assert len(records) == len(caps) == 48
last = 0
for r in records:
    spoken = r['spoken_text']
    for a, b in [('F P G A', 'FPGA'), ('H D M I', 'HDMI'), ('R G B', 'RGB'), ('R K', 'RK')]:
        spoken = spoken.replace(a, b)
    c = by_id[r['id']]
    assert re.sub(r'\s+', '', spoken) == re.sub(r'\s+', '', c['text'])
    assert r['start'] >= last - .001
    assert r['start'] + r['duration'] <= r['end'] + .025
    assert c['startMs'] / 1000 <= r['start'] + .001
    assert c['endMs'] / 1000 >= r['start'] + r['duration'] - .001
    assert Path(r['file']).is_file()
    last = r['start'] + r['duration']
assert sum(s['duration'] for s in timeline) == 318
meta = json.loads(subprocess.run([shutil.which('ffprobe'), '-v', 'error', '-show_streams', '-show_format', '-of', 'json', str(OUT)], capture_output=True, text=True, encoding='utf8', check=True).stdout)
v = next(s for s in meta['streams'] if s['codec_type'] == 'video')
a = next(s for s in meta['streams'] if s['codec_type'] == 'audio')
assert (v['width'], v['height'], v['avg_frame_rate'], v['nb_frames']) == (1920, 1080, '60/1', '19080')
assert abs(float(v['duration']) - 318) < .001
assert a['sample_rate'] == '48000' and a['channels'] == 2
font = ImageFont.truetype('C:/Windows/Fonts/msyh.ttc', 20)
for page in range(3):
    sheet = Image.new('RGB', (2560, 1552), '#0d1d29')
    d = ImageDraw.Draw(sheet)
    for i, c in enumerate(caps[page*16:(page+1)*16]):
        t = (c['startMs'] + c['endMs']) / 2000
        raw = subprocess.run([str(FF), '-v', 'error', '-ss', str(t), '-i', str(OUT), '-frames:v', '1', '-vf', 'scale=640:360', '-f', 'image2pipe', '-vcodec', 'png', '-'], capture_output=True, check=True).stdout
        x, y = (i % 4)*640, (i // 4)*388
        sheet.paste(Image.open(io.BytesIO(raw)), (x, y+28))
        d.text((x+8, y+2), f'{int(t)//60:02}:{t%60:05.2f}  {c["scene"]}', font=font, fill='white')
    sheet.save(P/f'全片画面核对_{page+1}.jpg', quality=94)
sheet = Image.new('RGB', (1920, 1164), '#0d1d29')
d = ImageDraw.Draw(sheet)
for i, t in enumerate([128.8, 129.6, 134.8, 137.5, 139.0, 141.5, 145.0, 151.0, 155.0]):
    raw = subprocess.run([str(FF), '-v', 'error', '-ss', str(t), '-i', str(OUT), '-frames:v', '1', '-vf', 'scale=640:360', '-f', 'image2pipe', '-vcodec', 'png', '-'], capture_output=True, check=True).stdout
    x, y = (i % 3)*640, (i // 3)*388
    sheet.paste(Image.open(io.BytesIO(raw)), (x, y+28))
    d.text((x+8, y+2), f'{int(t)//60:02}:{t%60:05.2f}', font=font, fill='white')
sheet.save(P/'自动画框改剪核对.jpg', quality=94)
report = dict(output=str(OUT), duration_seconds=318, dimensions=[1920,1080], video_frames=19080, export_fps=60, audio_sample_rate=48000, audio_channels=2, subtitles=48, subtitle_voice_words_match=True, voice_intervals_nonoverlapping=True, subtitles_cover_spoken_intervals=True, size_bytes=OUT.stat().st_size, annotations_source='真实既有录屏，未加载模型或连接开发板', source_function_audit='调查依据.txt')
(P/'成片核对.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf8')
print(json.dumps(report, ensure_ascii=True), flush=True)
