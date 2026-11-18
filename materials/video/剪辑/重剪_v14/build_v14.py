"""Remove only the opening editorial label; retain all v13 footage and audio."""
from pathlib import Path
import json
import subprocess


def main():
    p = Path(__file__).resolve().parent
    old = p.parent / '重剪_v13'
    ff = p.parent.parent / 'dataflow_film_20261028/.tools/imageio_ffmpeg/binaries/ffmpeg-win-x86_64-v7.1.exe'
    source = old / 'FPGA视觉系统_小样本价值补充_v13.mp4'
    out = p / 'FPGA视觉系统_开头文字修订_v14.mp4'
    original = (old / 'final.ass').read_text(encoding='utf-8-sig')
    lines = original.splitlines(keepends=True)
    removed = [line for line in lines if line.startswith('Dialogue:') and line.rstrip().endswith('}引子')]
    assert len(removed) == 1
    revised = ''.join(line for line in lines if line not in removed)
    assert '引子' not in revised
    (p / 'final.ass').write_text(revised, encoding='utf-8-sig')
    chapters = (old / 'chapters.ffmeta').read_text(encoding='utf8')
    assert chapters.count('title=引子') == 1
    (p / 'chapters.ffmeta').write_text(chapters.replace('title=引子', 'title=让工件自动分开'), encoding='utf8')
    with (p / 'render.log').open('w', encoding='utf8') as log:
        subprocess.run([
            str(ff), '-y', '-hide_banner', '-nostdin',
            '-i', str(old / 'video_inserted.mp4'), '-i', str(source), '-i', 'chapters.ffmeta',
            '-map', '0:v:0', '-map', '1:a:0', '-map_metadata', '2', '-map_chapters', '2',
            '-vf', 'ass=final.ass', '-c:v', 'h264_nvenc', '-preset', 'p5', '-cq', '19', '-b:v', '0',
            '-pix_fmt', 'yuv420p', '-r', '60', '-video_track_timescale', '60000',
            '-c:a', 'copy', '-t', '179.8', '-movflags', '+faststart', str(out)
        ], cwd=p, stdout=log, stderr=log, check=True)
    info = json.loads(subprocess.run([
        'ffprobe', '-v', 'error', '-show_streams', '-show_format', '-show_chapters', '-of', 'json', str(out)
    ], capture_output=True, text=True, encoding='utf8', check=True).stdout)
    v = next(s for s in info['streams'] if s['codec_type'] == 'video')
    assert v['nb_frames'] == '10788' and abs(float(info['format']['duration']) - 179.8) < .001
    assert all('引子' not in c.get('tags', {}).get('title', '') for c in info['chapters'])
    audio_hashes = []
    for file in [source, out]:
        audio_hashes.append(subprocess.run([
            str(ff), '-v', 'error', '-i', str(file), '-map', '0:a:0', '-c:a', 'copy',
            '-f', 'hash', '-hash', 'sha256', '-'
        ], capture_output=True, text=True, encoding='utf8', check=True).stdout.strip())
    assert audio_hashes[0] == audio_hashes[1]
    report = dict(duration_seconds=179.8, frames=10788, removed_visible_text='引子',
                  original_subtitle_lines_preserved=True, original_audio_packets_identical=True,
                  audio_sha256=audio_hashes[0], chapters=info['chapters'], output=str(out))
    (p / '核对.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf8')
    subprocess.run([str(ff), '-v', 'error', '-y', '-ss', '3.3', '-i', str(out), '-frames:v', '1',
                    str(p / '开头核对.png')], check=True)
    print(json.dumps(report, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
