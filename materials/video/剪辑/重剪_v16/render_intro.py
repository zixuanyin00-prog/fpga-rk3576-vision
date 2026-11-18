"""Use a real, continuous workcell overview for the four-second opening shot."""
from pathlib import Path
import io, json, subprocess
from PIL import Image, ImageDraw, ImageFont

P=Path(__file__).resolve().parent
M=P.parent
FF=M.parent/'dataflow_film_20261028/.tools/imageio_ffmpeg/binaries/ffmpeg-win-x86_64-v7.1.exe'
SOURCE=M/'广角总览.MOV'
OUT=P/'intro.mp4'
FILTER='setpts=PTS-STARTPTS,scale=1920:1080,setsar=1,fps=60,drawbox=x=0:y=930:w=iw:h=150:color=black@0.30:t=fill'
with (P/'intro_render.log').open('w',encoding='utf8') as log:
 subprocess.run([str(FF),'-y','-hide_banner','-nostdin','-ss','1','-t','4.1','-i',str(SOURCE),'-vf',FILTER,'-frames:v','240','-an','-c:v','h264_nvenc','-preset','p5','-cq','19','-b:v','0','-pix_fmt','yuv420p','-r','60','-video_track_timescale','60000','-movflags','+faststart',str(OUT)],stdout=log,stderr=log,check=True)
info=json.loads(subprocess.run(['ffprobe','-v','error','-show_streams','-show_format','-of','json',str(OUT)],capture_output=True,text=True,encoding='utf8',check=True).stdout)
v=info['streams'][0]
assert len(info['streams'])==1 and (v['width'],v['height'],v['avg_frame_rate'],v['nb_frames'])==(1920,1080,'60/1','240')
assert abs(float(info['format']['duration'])-4)<.001
record=dict(kind='footage',file=str(SOURCE),start=1,length=4,playback_speed=1,
            framing='Complete 3840x2160 source frame scaled to 1920x1080, no crop',
            source_video_avg_frame_rate='255600/2131',export_fps=60,
            description='Continuous workcell overview including HDMI, console laptop, camera, boards and vibrating bowl',
            filter=FILTER,output=str(OUT))
(P/'source_intro.json').write_text(json.dumps(record,ensure_ascii=False,indent=2),encoding='utf8')
sheet=Image.new('RGB',(1920,388),'#0d1d29');d=ImageDraw.Draw(sheet)
font=ImageFont.truetype('C:/Windows/Fonts/msyh.ttc',20)
for i,t in enumerate([.25,2,3.75]):
 raw=subprocess.run([str(FF),'-v','error','-ss',str(t),'-i',str(OUT),'-frames:v','1','-vf','scale=640:360','-f','image2pipe','-vcodec','png','-'],capture_output=True,check=True).stdout
 sheet.paste(Image.open(io.BytesIO(raw)),(i*640,28));d.text((i*640+7,2),f'{t}s',font=font,fill='white')
sheet.save(P/'intro_output_contact.jpg',quality=95)
print(json.dumps(record,ensure_ascii=True),flush=True)
