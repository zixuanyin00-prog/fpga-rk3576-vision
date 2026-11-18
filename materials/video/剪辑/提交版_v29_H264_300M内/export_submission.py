"""Encode the approved v28 film within the submission file-size limit."""
from pathlib import Path
import hashlib,json,subprocess

P=Path(__file__).resolve().parent
SOURCE=P.parent/'重剪_v28_清理剪辑标注/FPGA视觉系统_B简洁标注_v28.mp4'
OUT=P/'FPGA视觉系统_比赛提交版_H264_v29.mp4'
FF=P.parent.parent/'dataflow_film_20261028/.tools/imageio_ffmpeg/binaries/ffmpeg-win-x86_64-v7.1.exe'
LIMIT_BYTES=300_000_000


def probe(file):
    return json.loads(subprocess.run(['ffprobe','-v','error','-show_streams','-show_format','-show_chapters','-of','json',str(file)],capture_output=True,text=True,encoding='utf8',check=True).stdout)


def main():
    source=probe(SOURCE)
    video=next(s for s in source['streams'] if s['codec_type']=='video')
    args=[str(FF),'-y','-hide_banner','-nostdin','-i',str(SOURCE),'-map','0:v:0','-map','0:a:0','-map_metadata','0','-map_chapters','0',
          '-c:v','h264_nvenc','-preset','p6','-tune','hq','-profile:v','high','-level:v','4.2','-rc','vbr','-b:v','12500k','-maxrate','13000k','-bufsize','26000k',
          '-multipass','fullres','-spatial-aq','1','-temporal-aq','1','-aq-strength','8','-pix_fmt','yuv420p','-c:a','copy','-movflags','+faststart',str(OUT)]
    (P/'encode_command.json').write_text(json.dumps(args,ensure_ascii=False,indent=2),'utf8')
    with (P/'encode.log').open('w',encoding='utf8') as log:subprocess.run(args,stdout=log,stderr=log,check=True)
    result=probe(OUT);v=next(s for s in result['streams'] if s['codec_type']=='video');a=next(s for s in result['streams'] if s['codec_type']=='audio')
    assert OUT.stat().st_size<LIMIT_BYTES
    assert v['codec_name']=='h264' and v['codec_tag_string']=='avc1' and v['pix_fmt']=='yuv420p'
    assert (v['width'],v['height'],v['avg_frame_rate'],v['nb_frames'])==(video['width'],video['height'],video['avg_frame_rate'],video['nb_frames'])
    assert abs(float(result['format']['duration'])-float(source['format']['duration']))<.002 and float(result['format']['duration'])<180
    assert result['chapters']==source['chapters']
    audio_hashes=[]
    for file in [SOURCE,OUT]:
        digest=subprocess.run([str(FF),'-v','error','-i',str(file),'-map','0:a:0','-c:a','copy','-f','hash','-hash','sha256','-'],capture_output=True,text=True,encoding='utf8',check=True).stdout.strip();audio_hashes.append(digest)
    assert audio_hashes[0]==audio_hashes[1]
    subprocess.run([str(FF),'-v','error','-nostdin','-i',str(OUT),'-map','0:v:0','-map','0:a:0','-f','null','-'],check=True)
    digest=hashlib.sha256()
    with OUT.open('rb') as file:
        for chunk in iter(lambda:file.read(8*1024*1024),b''):digest.update(chunk)
    report=dict(output=str(OUT),source=str(SOURCE),container='MP4',video_codec='AVC / H.264',profile=v['profile'],codec_tag=v['codec_tag_string'],duration_seconds=float(result['format']['duration']),width=v['width'],height=v['height'],fps=v['avg_frame_rate'],frames=int(v['nb_frames']),size_bytes=OUT.stat().st_size,size_decimal_MB=round(OUT.stat().st_size/1_000_000,2),limit_bytes=LIMIT_BYTES,below_size_limit=True,below_180_seconds=True,audio_codec=a['codec_name'],audio_stream_copied_unchanged=True,audio_packet_sha256=audio_hashes[0],all_chapters_unchanged=True,decode_passed=True,sha256=digest.hexdigest())
    (P/'提交规格核对.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),'utf8')
    print(json.dumps(report,ensure_ascii=False),flush=True)


if __name__=='__main__':main()
