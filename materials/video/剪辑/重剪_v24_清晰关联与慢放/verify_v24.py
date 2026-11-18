"""Necessary checks and ten representative review frames for the v24 media export.

Run only after the final MP4, captions and audio mix have been created.
This script reads sources and writes review images/report; it never builds media.
"""
from fractions import Fraction
from pathlib import Path
import hashlib
import io
import json
import re
import subprocess
import wave

from PIL import Image, ImageDraw, ImageFont
import build_v24 as b


P = Path(__file__).resolve().parent
FPS, FRAMES, DURATION = 60, 10788, 179.8
MOMENTS = [
    (44, 'ISP 六项对照'), (48, 'ISP 六项对照'),
    (82, 'ID 880 跨帧关联'), (90, '多工件像素宽高'),
    (111, '姿态分拣慢放'), (115, '姿态分拣慢放'),
    (129, 'HDMI 进一步慢放'), (136, '分拣功能未开启'),
    (164, '类别分拣保留段'), (171, '收集画面继续'),
]


def read_json(file):
    return json.loads(Path(file).read_text(encoding='utf8'))


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def probe(file):
    return json.loads(subprocess.run(
        ['ffprobe', '-v', 'error', '-show_streams', '-show_format', '-of', 'json', str(file)],
        capture_output=True, text=True, encoding='utf8', check=True,
    ).stdout)


def clean_text(text):
    return re.sub(r'\s', '', text)


def ass_seconds(text):
    h, m, s = text.split(':')
    return int(h) * 3600 + int(m) * 60 + float(s)


def main():
    require(b.OUT.is_file(), f'Final export does not exist: {b.OUT}')
    meta = probe(b.OUT)
    video = next(s for s in meta['streams'] if s['codec_type'] == 'video')
    audio = next(s for s in meta['streams'] if s['codec_type'] == 'audio')
    require((video['width'], video['height'], Fraction(video['avg_frame_rate']), int(video['nb_frames']))
            == (1920, 1080, FPS, FRAMES), 'Final dimensions, FPS or frame count changed')
    require(abs(float(meta['format']['duration']) - DURATION) <= .002, 'Final duration must be 179.8 seconds')
    require(audio['sample_rate'] == '48000' and audio['channels'] == 2, 'Final audio must be 48 kHz stereo')

    timeline = read_json(P / 'timeline.json')
    by = {s['name']: s for s in timeline}
    cursor = 0
    for scene in timeline:
        require(abs(scene['start'] - cursor / FPS) < .0001, f'Timeline gap/overlap at {scene["name"]}')
        cursor += round(scene['duration'] * FPS)
    require(cursor == FRAMES, 'Timeline must total 10788 frames')
    require(by['isp_methods']['duration'] == 10.5, 'ISP scene must be 10.5 seconds')
    require(by['tracking_short']['duration'] == 5.2, 'Tracking scene must be 5.2 seconds')
    require({s['name']: s['duration'] for s in timeline if s['name'] in b.EVIDENCE} == b.EVIDENCE,
            'Evidence scene lengths differ from the v24 plan')

    slow_checks = [
        ('pose_sorting_slow', 109, 40, 3.5, [(0, 1, 1), (1, 1.75, .5), (1.75, 3.5, .25)],
         '高速整体演示（振动盘） (0-59s可用).MOV', 570),
        ('pose_slow', 121, 26, 4.75, [(0, 3.25, .5), (3.25, 4.75, .25)],
         '高速整体演示（HDMI）.MOV', 750),
    ]
    speed_report = {}
    for name, output_start, source_start, source_length, expected, filename, frames in slow_checks:
        scene = by[name]
        source = scene['source']
        segments = source['speed_segments']
        actual = [(s['start'], s['end'], s['speed']) for s in segments]
        require(actual == expected, f'{name}: source speed segments changed')
        require(scene['start'] == output_start and source['start'] == source_start
                and source['length'] == source_length and Path(source['file']).name == filename,
                f'{name}: source range or output start changed')
        require(Path(source['file']).is_file(), f'{name}: original recording missing')
        require(segments[0]['start'] == 0 and segments[-1]['end'] == source_length
                and all(a['end'] == z['start'] for a, z in zip(segments, segments[1:])),
                f'{name}: source segments must be continuous without repeats or gaps')
        lengths = [(Fraction(str(z)) - Fraction(str(a))) / Fraction(str(speed)) for a, z, speed in actual]
        require(sum(lengths) == Fraction(str(scene['duration']))
                and sum(lengths) * FPS == frames, f'{name}: speed duration/frame calculation is incorrect')
        clip = Path(scene['file'])
        marker = read_json(clip.with_suffix('.source.json'))
        require(marker['source'] == source and marker['frames'] == frames,
                f'{name}: rendered source signature does not match timeline')
        clip_video = next(s for s in probe(clip)['streams'] if s['codec_type'] == 'video')
        require(int(clip_video['nb_frames']) == frames and Fraction(clip_video['avg_frame_rate']) == FPS,
                f'{name}: rendered clip has wrong frames or FPS')
        speed_report[name] = dict(source_seconds=[source_start, source_start + source_length],
                                 speeds=[s[2] for s in actual], output_segment_frames=[int(x * FPS) for x in lengths],
                                 frames=frames, signature_matches=True)

    action, collect = by['category_action'], by['category_collect']
    source = action['source']
    require(round(action['duration'] * FPS) == 830 and source['start'] == 0
            and abs(source['length'] - 830 / FPS) < .0001 and source['playback_speed'] == 1
            and not source.get('segments'), 'category_action must retain only the first 830 source frames')
    require(Path(source['file']).resolve() == (b.OLD / 'scenes/category_action.mp4').resolve(),
            'category_action must use the retained prefix of the existing v23 scene')
    require(abs(action['start'] + action['duration'] - 168.5) < .0001
            and collect['start'] == 168.5, 'Collection must continue directly at 168.5 seconds')
    require(source['filters'] == ['null'] and round(collect['duration'] * FPS) == 378,
            'Retained action/collection footage filters or length changed')
    action_video = next(s for s in probe(Path(action['file']))['streams'] if s['codec_type'] == 'video')
    require(int(action_video['nb_frames']) == 830, 'category_action output must contain exactly 830 frames')

    voices = read_json(P / '配音生成记录.json')
    caps = read_json(P / 'captions.json')
    require(len({r['id'] for r in voices}) == len(voices) == len(caps)
            == len({r['id'] for r in caps}), 'Voice/caption IDs must be unique and one-to-one')
    require({r['id'] for r in voices} == {r['id'] for r in caps}, 'Voice and caption ID sets differ')
    voice_by = {r['id']: r for r in voices}
    voices = sorted(voices, key=lambda r: r['start'])
    caps = sorted(caps, key=lambda r: r['startMs'])
    for a, z in zip(voices, voices[1:]):
        require(a['start'] + a['duration'] <= z['start'] + .001, f'Voice overlap: {a["id"]} / {z["id"]}')
    for a, z in zip(caps, caps[1:]):
        require(a['endMs'] <= z['startMs'], f'Caption overlap: {a["id"]} / {z["id"]}')
    for cap in caps:
        voice = voice_by[cap['id']]
        require(clean_text(cap['text']) == clean_text(b.normalize(voice['spoken_text'])),
                f'Caption differs from spoken-text record: {cap["id"]}')
        require(0 <= cap['startMs'] < cap['endMs'] <= DURATION * 1000,
                f'Invalid caption interval: {cap["id"]}')
        require(cap['startMs'] / 1000 <= voice['start'] + .002
                and cap['endMs'] / 1000 + .002 >= voice['start'] + voice['duration'],
                f'Caption does not cover voice interval: {cap["id"]}')
        voice_file = Path(voice['file'])
        require(voice_file.is_file(), f'Voice file missing: {voice_file}')
        if voice_file.suffix.lower() == '.wav':
            with wave.open(str(voice_file), 'rb') as wav:
                actual_duration = wav.getnframes() / wav.getframerate()
            require(abs(actual_duration - voice['duration']) <= .03, f'Voice duration metadata differs: {cap["id"]}')

    srt = (P / '全片字幕.srt').read_text(encoding='utf-8-sig').strip().split('\n\n')
    require(len(srt) == len(caps), 'SRT subtitle count differs')
    for block, cap in zip(srt, caps):
        lines = block.splitlines()
        a, z = [ass_seconds(x.replace(',', '.')) for x in lines[1].split(' --> ')]
        require(abs(a * 1000 - cap['startMs']) <= 1 and abs(z * 1000 - cap['endMs']) <= 1
                and clean_text('\n'.join(lines[2:])) == clean_text(cap['text']),
                f'SRT differs from caption JSON: {cap["id"]}')

    ass = (P / 'final.ass').read_text(encoding='utf-8-sig')
    require(all(t in ass for t in ['分拣功能未开启', '0.5× 慢放', '0.25× 慢放', '92.87%']),
            'Expected final text/speed labels are missing')
    require(not any(t in ass for t in ['实验室空压机气流过大', '气流过大', '提示词', '引子', '不表示', '零漏检', '完美筛选']),
            'Removed gas hint or unwanted text reappeared')
    narration = []
    for line in ass.splitlines():
        if line.startswith('Dialogue:'):
            fields = line.split(',', 9)
            start, end = ass_seconds(fields[1]), ass_seconds(fields[2])
            require(not (abs(start - 164) < .01 and abs(end - 168.4) < .01
                         and 'm 0 0 l 1250 0 1250 78 0 78' in fields[9]),
                    'Removed gas hint background plate reappeared')
            if int(fields[0].split(':')[1]) == 1:
                text = re.sub(r'\{[^}]*\}', '', fields[9]).replace(r'\N', '\n').replace(r'\h', ' ')
                require(0 <= start < end <= DURATION, 'Invalid ASS narration interval')
                narration.append((start, end, clean_text(text)))
    narration.sort()
    require(narration, 'No narration subtitles found in final.ass')
    for a, z in zip(narration, narration[1:]):
        require(a[1] <= z[0] + .001, 'ASS narration subtitle overlap')
    for cap in caps:
        matching = [e for e in narration if e[2] == clean_text(cap['text'])
                    and e[0] >= cap['startMs'] / 1000 - .006 and e[1] <= cap['endMs'] / 1000 + .006]
        require(matching and abs(matching[0][0] - cap['startMs'] / 1000) <= .006
                and abs(matching[-1][1] - cap['endMs'] / 1000) <= .006
                and all(abs(a[1] - z[0]) <= .001 for a, z in zip(matching, matching[1:])),
                f'ASS does not preserve caption text/timing: {cap["id"]}')

    trace = read_json(P / 'tracking_evidence/sequence_native/traceable.json')
    require(trace['track_id'] == 880 and trace['selected_source_frames'] == [89270, 89278, 89284],
            'Tracking identity or three source frames changed')
    require({r['session_id'] for r in trace['records']} == {trace['session_id']}
            and {r['object_id'] for r in trace['records']} == {trace['object_id']}, 'Tracking identity scope differs')
    for name in ['source_frames_jsonl', 'source_capture_summary']:
        item = trace[name]
        require(hashlib.sha256(Path(item['path']).read_bytes()).hexdigest() == item['sha256'],
                f'Original tracking metadata changed: {name}')
    original_frames = {r['source_frame_id']: r for r in
                       (json.loads(line) for line in Path(trace['source_frames_jsonl']['path']).read_text(encoding='utf8').splitlines())}
    for record in trace['records']:
        source_frame = record['source_frame_id']
        frame = original_frames[source_frame]
        row = next(r for r in frame['rows'] if r['track_id'] == 880)
        require(row['session_id'] == trace['session_id'] and row['object_id'] == trace['object_id']
                and row['confirmed'] and not row['association_uncertain'] and row['classification']['accepted']
                and row['classification_temporal']['accepted'] and row['tracking']['pixel_identity_verified'],
                f'Tracking source observation is not the confirmed ID880: {source_frame}')
        require(row['raw_box'] == record['bbox_source_xyxy'] and frame['sha256'] == record['raw_source_sha256'],
                f'Tracking box or source hash differs from original log: {source_frame}')
        folder = P / 'tracking_evidence/sequence_native'
        raw = (folder / f'track880_source{source_frame}.rgb').read_bytes()
        require(hashlib.sha256(raw).hexdigest() == record['raw_source_sha256'], 'Tracking RGB hash changed')
        with Image.open(folder / f'track880_source{source_frame}_native.png') as image:
            require(image.size == (793, 317) and image.convert('RGB').tobytes() == raw,
                    'Tracking PNG differs from original RGB pixels')

    visible = read_json(P / 'visible_manifest.json')
    freeze = read_json(P / 'evidence_scenes_v24.freeze.json')
    renderer_hash = hashlib.sha256((P / freeze['file']).read_bytes()).hexdigest()
    require(renderer_hash == freeze['sha256'] == visible['renderer']['sha256'], 'Frozen scene source hash changed')
    require(visible['facts']['tracking_short']['track_id'] == 880
            and visible['facts']['tracking_short']['source_ids'] == [89270, 89278, 89284], 'Scene tracking manifest differs')
    for item in visible['sources']:
        require(hashlib.sha256((P / item['file']).read_bytes()).hexdigest() == item['sha256'],
                f'Source evidence changed: {item["file"]}')
    require(not any('气流过大' in text for group in visible['scene_text'].values() for text in group),
            'Removed gas hint reappeared in scene text')

    # One media decode catches damaged export packets; no frame-by-frame accuracy audit.
    subprocess.run([str(b.FF), '-v', 'error', '-nostdin', '-err_detect', 'explode', '-i', str(b.OUT),
                    '-map', '0:v:0', '-map', '0:a:0', '-f', 'null', '-'], check=True)

    review = P / '代表帧审看'
    review.mkdir(exist_ok=True)
    sheet = Image.new('RGB', (1920, 1552), '#cbdde9')
    draw = ImageDraw.Draw(sheet)
    font = ImageFont.truetype('C:/Windows/Fonts/msyh.ttc', 23)
    for i, (seconds, label) in enumerate(MOMENTS):
        data = subprocess.run([str(b.FF), '-v', 'error', '-nostdin', '-ss', str(seconds), '-i', str(b.OUT),
                               '-frames:v', '1', '-f', 'image2pipe', '-vcodec', 'png', '-'],
                              capture_output=True, check=True).stdout
        (review / f'{seconds:03d}s.png').write_bytes(data)
        with Image.open(io.BytesIO(data)) as image:
            thumb = image.convert('RGB').resize((640, 360), Image.Resampling.LANCZOS)
        x, y = i % 3 * 640, i // 3 * 388
        draw.text((x + 8, y + 1), f'{seconds:03d}s  {label}', font=font, fill='#183747')
        sheet.paste(thumb, (x, y + 28))
    sheet_path = P / '成片审看_v24.jpg'
    sheet.save(sheet_path, quality=95)

    report = dict(output=str(b.OUT), duration=DURATION, width=1920, height=1080, fps=FPS, frames=FRAMES,
                  narration_cues=len(voices), subtitle_cues=len(caps), subtitles_match_spoken_records=True,
                  voice_overlaps=False, subtitle_overlaps=False, ass_text_and_timing_match=True, srt_matches=True,
                  decode_passed=True, evidence_scene_seconds=b.EVIDENCE, tracking_identity=880,
                  tracking_source_frame_ids=trace['selected_source_frames'], tracking_native_pixels_unchanged=True,
                  continuous_slow_sources=speed_report, category_action_retained_source_frames=[0, 830],
                  category_collect_start=168.5, removed_original_v23_range_seconds=[164, 168.5],
                  original_gas_hint_removed=True, source_evidence_hashes_match=True,
                  scene_source_sha256=renderer_hash, review_moments=[t for t, _ in MOMENTS],
                  contact_sheet=str(sheet_path), visual_review_pending=True,
                  verification_scope='Necessary local media, timing, source hash and subtitle record checks; no model execution, hardware or accuracy audit.',
                  size_bytes=b.OUT.stat().st_size)
    (P / '成片核对.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf8')
    print(json.dumps(report, ensure_ascii=True), flush=True)


if __name__ == '__main__':
    main()
