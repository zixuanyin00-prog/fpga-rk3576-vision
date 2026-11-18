"""Real synchronized control and HDMI footage, with readable crops and annotations."""
from pathlib import Path
import argparse, json, subprocess
from PIL import Image, ImageDraw, ImageFont

P=Path(__file__).resolve().parent
M=P.parent
FF=M.parent/'dataflow_film_20261028/.tools/imageio_ffmpeg/binaries/ffmpeg-win-x86_64-v7.1.exe'
CONSOLE=next(M.glob('控制台全流程*.mp4'))
HDMI=M/'实拍ISP使用.MOV'
ENC=['-an','-c:v','h264_nvenc','-preset','p5','-cq','19','-b:v','0','-pix_fmt','yuv420p','-r','60','-video_track_timescale','60000']
PHASES=[dict(name='gain',start=38,length=4,detail=[370,1130,1270,330],title='自动增益调节',highlight=[835,1318,375,76]),
        dict(name='processing',start=102,length=6,detail=[790,1080,760,244],title='彩色边缘叠加',highlight=[832,1176,370,51])]
BG='#0d1d29';INK='#edf5f3';TEAL='#78dfcb';GOLD='#efcf8a';MUTED='#a5bdc7'


def overlay(phase):
    scale=2
    image=Image.new('RGBA',(1920*scale,1080*scale),(0,0,0,0))
    d=ImageDraw.Draw(image)
    def text(value,x,y,size=28,color=INK):
        font=ImageFont.truetype('C:/Windows/Fonts/msyh.ttc',round(size*scale))
        d.text((x*scale,y*scale),value,font=font,fill=color,anchor='mm')
    def rect(box,color,width=2):
        d.rectangle(tuple(round(v*scale) for v in box),outline=color,width=width*scale)
    text('PC 控制台',396,38,32)
    text('RK HDMI · 实时成像',1360,38,32)
    text(phase['title'],396,608,29,GOLD)
    text('成像细节',1360,724,29,GOLD)
    rect([15,83,777,583],TEAL)
    rect([15,633,777,879],GOLD)
    rect([815,83,1905,704],TEAL)
    rect([815,747,1905,915],GOLD)
    # Matching gold outlines associate the actual control field and the enlarged image region.
    x,y,w,h=phase['detail'];sx,sy,sw,sh=phase['highlight']
    k=760/w
    display_h=round(h*k);dy=(244-display_h)//2
    rect([16+(sx-x)*k,634+dy+(sy-y)*k,16+(sx-x+sw)*k,634+dy+(sy-y+sh)*k],GOLD,3)
    # The right crop is a view of these real HDMI pixels, not a synthesized result.
    rect([941,422,1629,528],GOLD,2)
    d.line([(941*scale,528*scale),(804*scale,716*scale),(816*scale,748*scale)],fill=GOLD,width=2*scale)
    text('配置下发：控制台 → RK → FPGA',396,894,25,GOLD)
    text('状态回读：控制台 ← RK ← FPGA',396,923,25,TEAL)
    image=image.resize((1920,1080),Image.Resampling.LANCZOS)
    file=P/f'control_{phase["name"]}_overlay.png'
    image.save(file)
    return file


def preview(phase):
    frames=[]
    for source in [CONSOLE,HDMI]:
        raw=subprocess.run([str(FF),'-v','error','-ss',str(phase['start']+2),'-i',str(source),'-frames:v','1','-f','image2pipe','-vcodec','png','-'],capture_output=True,check=True).stdout
        import io
        frames.append(Image.open(io.BytesIO(raw)).convert('RGB'))
    c,h=frames
    canvas=Image.new('RGB',(1920,1080),BG)
    canvas.paste(c.crop((0,0,2440,1600)).resize((760,498),Image.Resampling.LANCZOS),(16,84))
    x,y,w,height=phase['detail']
    display_h=round(height*760/w);dy=(244-display_h)//2
    canvas.paste(c.crop((x,y,x+w,y+height)).resize((760,display_h),Image.Resampling.LANCZOS),(16,634+dy))
    canvas.paste(h.crop((140,10,1880,1000)).resize((1088,619),Image.Resampling.LANCZOS),(816,84))
    canvas.paste(h.crop((340,550,1440,718)).resize((1088,166),Image.Resampling.LANCZOS),(816,748))
    canvas.paste(Image.open(overlay(phase)),(0,0),Image.open(overlay(phase)))
    canvas.save(P/f'control_{phase["name"]}_preview.png')


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--preview',action='store_true');args=parser.parse_args()
    for phase in PHASES:
        preview(phase)
    if args.preview:return
    parts=[]
    for phase in PHASES:
        name=phase['name'];length=phase['length'];x,y,w,h=phase['detail']
        display_h=round(h*760/w);dy=(244-display_h)//2
        graph=(f'[0:v]setpts=PTS-STARTPTS,fps=60,split=2[c][d];'
            '[c]crop=2440:1600:0:0,scale=760:498,setsar=1[context];'
            f'[d]crop={w}:{h}:{x}:{y},scale=760:{display_h},pad=760:244:0:{dy}:color=0x0d1d29,setsar=1[detail];'
            '[1:v]setpts=PTS-STARTPTS,fps=60,split=2[h][r];'
            '[h]crop=1740:990:140:10,scale=1088:619,setsar=1[physical];'
            '[r]crop=1100:168:340:550,scale=1088:166,setsar=1[roi];'
            f'color=c=0x0d1d29:s=1920x1080:r=60:d={length}[bg];'
            '[bg][context]overlay=16:84[a];[a][detail]overlay=16:634[b];'
            '[b][physical]overlay=816:84[c2];[c2][roi]overlay=816:748[d2];'
            f'[d2][2:v]overlay=0:0:shortest=1,trim=duration={length},setpts=PTS-STARTPTS[v]')
        out=P/f'control_{name}.mp4'
        with (P/f'control_{name}.log').open('w',encoding='utf8') as log:
            subprocess.run([str(FF),'-y','-hide_banner','-nostdin',
                '-ss',str(phase['start']),'-t',str(length+.1),'-i',str(CONSOLE),
                '-ss',str(phase['start']),'-t',str(length+.1),'-i',str(HDMI),
                '-loop','1','-framerate','60','-i',str(overlay(phase)),
                '-filter_complex',graph,'-map','[v]','-frames:v',str(length*60),*ENC,str(out)],stdout=log,stderr=log,check=True)
        parts.append(out)
        print(name+' rendered',flush=True)
    concat=P/'control_concat.txt';concat.write_text(''.join("file '"+f.as_posix()+"'\n" for f in parts),encoding='utf8')
    subprocess.run([str(FF),'-v','error','-y','-f','concat','-safe','0','-i',str(concat),'-map','0:v:0','-c:v','copy','-an','-movflags','+faststart',str(P/'console_image.mp4')],check=True)
    provenance=dict(kind='synchronized_layout',duration=10,console=str(CONSOLE),physical_hdmi=str(HDMI),phases=PHASES,
        synchronization='Both source files use the same original start timestamp at normal speed',
        crops_remove='Top runtime guard is scrolled out in both chosen intervals; rightmost AI buttons are outside x2440',
        visible_labels=['PC 控制台','RK HDMI · 实时成像','自动增益调节','彩色边缘叠加','成像细节','配置下发：控制台 → RK → FPGA','状态回读：控制台 ← RK ← FPGA'])
    (P/'source_console_layout.json').write_text(json.dumps(provenance,ensure_ascii=False,indent=2),encoding='utf8')
    print('console_image.mp4 complete: 10s / 600 frames',flush=True)


if __name__=='__main__':main()
