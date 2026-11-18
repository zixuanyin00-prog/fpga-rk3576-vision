"""Create a light preview, inspect the encoded movie, and package editable sources."""
from pathlib import Path
import hashlib
import json
import shutil
import subprocess
import zipfile
from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent
OUT = ROOT / 'output'
MASTER = OUT / 'dataflow-v2-tree-4k60.mp4'
PREVIEW = OUT / 'dataflow-v2-tree-preview-1080p60.mp4'
LOCAL_FFMPEG = ROOT.parent / '.tools/imageio_ffmpeg/binaries/ffmpeg-win-x86_64-v7.1.exe'
FFMPEG = str(LOCAL_FFMPEG) if LOCAL_FFMPEG.exists() else shutil.which('ffmpeg')
FFPROBE = shutil.which('ffprobe')

def run(args):
    subprocess.run([str(x) for x in args], check=True)

def probe(file):
    return json.loads(subprocess.check_output([FFPROBE, '-v', 'error', '-show_streams', '-show_format', '-of', 'json', str(file)], encoding='utf-8'))

def digest(file):
    with file.open('rb') as f:
        return hashlib.file_digest(f, 'sha256').hexdigest()

master_info = probe(MASTER)
video = next(s for s in master_info['streams'] if s['codec_type'] == 'video')
assert (video['width'], video['height'], video['avg_frame_rate'], int(video['nb_frames'])) == (3840, 2160, '60/1', 7800)
assert abs(float(video['duration']) - 130) < .02

encoder = ['-c:v', 'h264_nvenc', '-preset', 'p5', '-cq', '21', '-b:v', '0'] if LOCAL_FFMPEG.exists() else ['-c:v', 'libx264', '-preset', 'fast', '-crf', '20']
run([FFMPEG, '-hide_banner', '-loglevel', 'warning', '-y', '-i', MASTER,
     '-vf', 'scale=1920:1080:flags=lanczos', *encoder, '-pix_fmt', 'yuv420p',
     '-c:a', 'copy', '-movflags', '+faststart', PREVIEW])
print('1080p preview encoded', flush=True)

# Decode the complete video; hashes of small decoded frames verify frame count
# and quantify frame changes without storing thousands of large bitmaps.
hashes_file = OUT / 'decoded-frames.md5'
run([FFMPEG, '-hide_banner', '-loglevel', 'warning', '-y', '-i', MASTER,
     '-map', '0:v:0', '-vf', 'scale=320:180', '-fps_mode', 'passthrough',
     '-f', 'framemd5', hashes_file])
hashes = [line.rsplit(',', 1)[-1].strip() for line in hashes_file.read_text().splitlines()
          if line and not line.startswith('#')]
assert len(hashes) == 7800
print('All 7800 master frames decoded', flush=True)

review = OUT / 'encoded-review'
review.mkdir(exist_ok=True)
times = [3, 17, 23, 30, 43, 50, 61, 77, 85, 90, 97, 104, 116, 125, 129]
sheet = Image.new('RGB', (1920, 1940), '#080c12')
font = ImageFont.truetype('C:/Windows/Fonts/consola.ttf', 20)
for i, t in enumerate(times):
    image = review / f'{t:02d}s.png'
    run([FFMPEG, '-hide_banner', '-loglevel', 'error', '-y', '-ss', t, '-i', MASTER,
         '-frames:v', '1', '-vf', 'scale=640:360', image])
    with Image.open(image) as im:
        x, y = i % 3 * 640, i // 3 * 388
        sheet.paste(im, (x, y))
        ImageDraw.Draw(sheet).text((x+14, y+364), f'{t:02d} s', font=font, fill='#91a6b5')
sheet.save(OUT / 'film-contact-sheet.jpg', quality=95)
run([FFMPEG, '-hide_banner', '-loglevel', 'error', '-y', '-ss', '23', '-i', MASTER,
     '-frames:v', '1', '-vf', 'scale=1920:1080', OUT / 'poster.jpg'])

preview_info = probe(PREVIEW)
preview_video = next(s for s in preview_info['streams'] if s['codec_type'] == 'video')
assert (preview_video['width'], preview_video['height'], preview_video['avg_frame_rate'], int(preview_video['nb_frames'])) == (1920, 1080, '60/1', 7800)
report = {
    'passed': True,
    'master': {'file': MASTER.name, 'width': 3840, 'height': 2160, 'fps': 60, 'duration_seconds': 130,
               'frames': 7800, 'bytes': MASTER.stat().st_size, 'sha256': digest(MASTER)},
    'preview': {'file': PREVIEW.name, 'width': 1920, 'height': 1080, 'fps': 60, 'duration_seconds': 130,
                'frames': 7800, 'bytes': PREVIEW.stat().st_size, 'sha256': digest(PREVIEW)},
    'audio': [dict(codec=s['codec_name'], channels=s['channels'], sample_rate=s['sample_rate'], duration=s['duration'])
              for s in master_info['streams'] if s['codec_type'] == 'audio'],
    'decode': {'all_frames_decoded': len(hashes), 'distinct_downscaled_frame_hashes': len(set(hashes)),
               'adjacent_identical_downscaled_frames': sum(a == b for a, b in zip(hashes, hashes[1:]))},
    'visual_review_times_seconds': times,
    'scope': 'Encoded-film verification only; no board, inference model, GPIO or display acceptance claim.'
}
(OUT / 'media-verification.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')

refs = ['three_stream_20261028/README.md', 'three_stream_20261028/host/ABI.md',
        'three_stream_20261028/runtime/full_roi_runtime.py', 'three_stream_20261028/runtime/rect_pipeline.py',
        'VisionTrainer/README.md', 'VisionTrainer/console_worker.py',
        'VisionTrainer/console_workflow.py', 'VisionTrainer/console_model_deploy.py',
        'VisionTrainer/core/model.py', 'VisionTrainer/core/training.py',
        'three_stream_20261028/runtime/model_input_adapter.py',
        'three_stream_20261028/runtime/ordered_whole_frames.py',
        'three_stream_20261028/runtime/pose_consensus.py']
sources = ['renderer.js', 'render.cjs', 'preview.html', 'audio.py', 'verify-preview.cjs', 'finish.py',
           'package.json', 'package-lock.json', 'README.md', 'soundtrack.m4a']
manifest = {'architecture_basis': [dict(path=r, sha256=digest(ROOT.parent.parent/r)) for r in refs],
            'film_sources': [dict(path=r, sha256=digest(ROOT/r)) for r in sources],
            'model_weights_used': False, 'board_connected': False, 'generated_images_are_schematic': True, 'tree_nodes': 93, 'tree_edges': 95, 'continuous_camera': True}
(ROOT / 'source-manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
with zipfile.ZipFile(OUT / 'dataflow-v2-tree-source.zip', 'w', zipfile.ZIP_DEFLATED) as archive:
    for name in sources + ['source-manifest.json', '.gitignore', 'output/preview-verification.json', 'output/tree-verification.json', 'output/media-verification.json']:
        archive.write(ROOT/name, 'dataflow-v2-tree/'+name)
print(json.dumps(report, ensure_ascii=False, indent=2), flush=True)
