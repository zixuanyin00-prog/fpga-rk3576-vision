"""Frame-exact crossfades that preserve the authored video/audio timeline."""
from pathlib import Path
from fractions import Fraction
import argparse
import json
import shutil
import subprocess

P = Path(__file__).resolve().parent
FFMPEG = P.parent.parent / 'dataflow_film_20261028/.tools/imageio_ffmpeg/binaries/ffmpeg-win-x86_64-v7.1.exe'
FFPROBE = shutil.which('ffprobe')
FPS = 60
HALF = 12
BRIDGE = HALF * 2
ENC = [
    '-an', '-c:v', 'h264_nvenc', '-preset', 'p5', '-cq', '19', '-b:v', '0',
    '-pix_fmt', 'yuv420p', '-r', str(FPS), '-video_track_timescale', '60000',
]


def probe(file, expected_frames):
    """Probe the media boundary; invalid or incomplete outputs are not reusable."""
    if not file.is_file() or file.stat().st_size == 0:
        return False
    result = subprocess.run(
        [FFPROBE, '-v', 'error', '-select_streams', 'v:0', '-show_entries',
         'stream=codec_name,width,height,avg_frame_rate,nb_frames,duration',
         '-of', 'json', str(file)],
        capture_output=True, text=True, encoding='utf8',
    )
    if result.returncode:
        return False
    streams = json.loads(result.stdout)['streams']
    if not streams:
        return False
    stream = streams[0]
    return (
        stream['codec_name'] == 'h264'
        and stream['width'] == 1920 and stream['height'] == 1080
        and Fraction(stream['avg_frame_rate']) == FPS
        and stream.get('nb_frames') == str(expected_frames)
        and abs(float(stream['duration']) - expected_frames / FPS) < .001
    )


def render(file, inputs, graph, frames, signature, parts):
    marker = file.with_suffix('.source.json')
    if marker.is_file() and json.loads(marker.read_text(encoding='utf8')) == signature and probe(file, frames):
        print(f'reuse {file.name} ({frames} frames)', flush=True)
        return
    log = parts / (file.stem + '.log')
    with log.open('w', encoding='utf8') as stream:
        subprocess.run(
            [str(FFMPEG), '-y', '-hide_banner', '-nostdin', *inputs,
             '-filter_complex', graph, '-map', '[v]', '-frames:v', str(frames),
             *ENC, str(file)],
            cwd=P, stdout=stream, stderr=stream, check=True,
        )
    if not probe(file, frames):
        raise RuntimeError(f'Incorrect output length or format: {file}; see {log}')
    marker.write_text(json.dumps(signature, ensure_ascii=False, indent=2), encoding='utf8')
    print(f'complete {file.name} ({frames} frames)', flush=True)


def bridge_graph(left_frames):
    # fps must precede tpad: an fps filter placed after tpad uses the trimmed
    # stream's original EOF timestamp and discards the synthetic hold frames.
    return (
        f'[0:v]trim=start_frame={left_frames-HALF}:end_frame={left_frames},'
        f'setpts=N/({FPS}*TB),setsar=1,fps={FPS},'
        f'tpad=stop_mode=clone:stop={HALF},trim=end_frame={BRIDGE},setpts=N/({FPS}*TB)[a];'
        f'[1:v]trim=start_frame=0:end_frame={HALF},setpts=N/({FPS}*TB),'
        f'setsar=1,fps={FPS},tpad=start_mode=clone:start={HALF},'
        f'trim=end_frame={BRIDGE},setpts=N/({FPS}*TB)[b];'
        "[a][b]blend=all_expr='A*(1-T/0.4)+B*(T/0.4)':shortest=1,"
        f'trim=end_frame={BRIDGE},setpts=N/({FPS}*TB)[v]'
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--timeline', type=Path, default=P / 'timeline.json')
    args = parser.parse_args()
    if not FFMPEG.is_file():
        raise FileNotFoundError(FFMPEG)
    if not FFPROBE:
        raise FileNotFoundError('ffprobe must be available on PATH')

    timeline = json.loads(args.timeline.read_text(encoding='utf-8-sig'))
    if not isinstance(timeline, list) or not timeline:
        raise ValueError('timeline.json must contain a nonempty ordered list of clips')
    clips = []
    total = 0
    for index, row in enumerate(timeline):
        source = Path(row['file'])
        source = (P / source).resolve() if not source.is_absolute() else source.resolve()
        raw_frames = float(row['duration']) * FPS
        frames = round(raw_frames)
        if abs(raw_frames - frames) > .001 or frames <= BRIDGE:
            raise ValueError(f'Clip {index} must have a whole-frame duration longer than {BRIDGE} frames')
        if abs(float(row['start']) * FPS - total) > .001:
            raise ValueError(f'Clip {index} is not contiguous with the authored timeline')
        if not probe(source, frames):
            raise ValueError(f'Clip {index} has an unexpected frame count, duration or format: {source}')
        stat = source.stat()
        clips.append(dict(
            name=row['name'], file=source, frames=frames,
            source=dict(file=str(source), size=stat.st_size, mtime_ns=stat.st_mtime_ns),
        ))
        total += frames
    if total != 326 * FPS:
        raise ValueError(f'Expected 326 seconds / 19560 frames; timeline contains {total} frames')

    parts = P / 'transition_parts'
    parts.mkdir(exist_ok=True)
    order = []
    expected_parts = []
    for index, clip in enumerate(clips):
        start = HALF if index else 0
        end = clip['frames'] - (HALF if index < len(clips) - 1 else 0)
        count = end - start
        body = parts / f'body_{index:02}.mp4'
        signature = dict(version=1, kind='body', source=clip['source'], start=start, end=end, fps=FPS)
        graph = f'[0:v]trim=start_frame={start}:end_frame={end},setpts=PTS-STARTPTS,setsar=1,fps={FPS}[v]'
        render(body, ['-i', str(clip['file'])], graph, count, signature, parts)
        order.append(body)
        expected_parts.append(count)
        if index == len(clips) - 1:
            continue

        next_clip = clips[index + 1]
        bridge = parts / f'bridge_{index:02}.mp4'
        # The first half advances A's final original frames while B holds its
        # opening frame. The second half holds A's last frame as B advances.
        # Each cut therefore still occupies exactly its original 24 frames.
        graph = bridge_graph(clip['frames'])
        signature = dict(
            version=2, kind='bridge', left=clip['source'], right=next_clip['source'],
            half_frames=HALF, blend_seconds=BRIDGE / FPS, fps=FPS,
        )
        render(bridge, ['-i', str(clip['file']), '-i', str(next_clip['file'])], graph, BRIDGE, signature, parts)
        order.append(bridge)
        expected_parts.append(BRIDGE)

    if sum(expected_parts) != total:
        raise RuntimeError('Transition construction changed the total frame count')
    concat = parts / 'concat.txt'
    concat.write_text(''.join("file '" + f.as_posix().replace("'", "'\\''") + "'\n" for f in order), encoding='utf8')
    output = P / 'video_transitions.mp4'
    with (parts / 'assemble.log').open('w', encoding='utf8') as log:
        subprocess.run(
            [str(FFMPEG), '-y', '-hide_banner', '-nostdin', '-f', 'concat', '-safe', '0',
             '-i', str(concat), '-map', '0:v:0', '-c:v', 'copy', '-an',
             '-movflags', '+faststart', str(output)],
            cwd=P, stdout=log, stderr=log, check=True,
        )
    if not probe(output, total):
        raise RuntimeError('Final transition video is not exactly 326 seconds / 19560 frames')
    report = dict(
        output=str(output), duration=total / FPS, frames=total, fps=FPS,
        clips=len(clips), transitions=len(clips) - 1,
        transition_frames=BRIDGE, transition_seconds=BRIDGE / FPS,
        authored_cut_times_preserved=True,
        parts=[dict(file=str(f), frames=n) for f, n in zip(order, expected_parts)],
    )
    (parts / 'verification.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf8')
    print(f'COMPLETE {output.name}: {total} frames, {total/FPS:.3f}s', flush=True)


if __name__ == '__main__':
    main()
