                                                              

import argparse
import json
from pathlib import Path
import os
import sys
import time
import gi

gi.require_version('Gtk', '3.0')
gi.require_version('Gdk', '3.0')
gi.require_version('GdkX11', '3.0')
from gi.repository import Gdk, GdkX11, GLib, Gtk

from hdmi_auto import AutoAdjustment
from hdmi_controls import CameraControls
from hdmi_x11 import PreviewWindow


STYLE = b'''
window, .vision { background: #111b2a; color: #edf5ff; font-size: 14px; }
.panel { border: 1px solid #30455d; padding: 12px 14px; }
.heading { color: #edf5ff; font-size: 22px; font-weight: 700; }
.section { color: #84eccb; font-weight: 600; margin-top: 8px; }
.hint { color: #b6c5d6; font-size: 12px; }
button, entry, spinbutton, combobox button {
    background: #1b2a3e; color: #edf5ff; border: 1px solid #30455d;
    border-radius: 6px; box-shadow: none; text-shadow: none; min-height: 29px;
}
button:hover, entry:focus, combobox button:focus, spinbutton:focus { border-color: #84eccb; }
button.selected, button.primary { background: #84eccb; color: #111b2a; border-color: #84eccb; }
button:disabled, entry:disabled, combobox button:disabled, spinbutton:disabled {
    background: #182233; color: #8192a7; border-color: #26364a;
}
checkbutton check { background: #1b2a3e; color: #111b2a; border-color: #638397; }
checkbutton check:checked { background: #84eccb; }
'''

ISP_FIELDS = {
    'exposure_us': ('曝光时间 · μs', 10, 16000, 100),
    'gain_db': ('模拟增益 · dB', 0, 30, 0.3),
    'digital_gain': ('数字增益 · Q8', 64, 1024, 8),
    'brightness': ('亮度', -100, 100, 1),
    'contrast': ('对比度 · Q8', 76, 768, 8),
    'rg': ('白平衡 R · Q8', 128, 512, 8),
    'bg': ('白平衡 B · Q8', 128, 512, 8),
    'analog_ceiling_db': ('模拟增益上限 · dB', 0, 30, 0.3),
    'digital_ceiling': ('数字增益上限 · 倍', 1, 4, 0.1),
}
QUICK_ISP_COLUMNS = {'exposure_us': 0, 'brightness': 0, 'gain_db': 1, 'contrast': 1}
AUTO_MODES = {
    'analog_gain': '模拟增益', 'digital_gain': '数字增益',
    'brightness': '亮度', 'contrast': '对比度', 'histogram': '直方图',
}
STAGES = {
    'gaussian': '高斯平滑', 'histogram': '直方图亮度映射', 'foreground': '前景分割',
    'binary_morphology': '前景掩码形态学', 'sobel': 'Sobel 边缘',
    'edge_cleanup': '边缘邻域清理', 'edge_overlay': '彩色边缘叠加',
    'background_shade': '背景压暗', 'feature_statistics': '特征统计',
    'grayscale_morphology': '灰度形态学融合', 'edge_closing': '边缘闭运算',
    'mask_boundary': '前景内边界',
}
VIEW_NAMES = ('处理后的 RGB', '基础 ISP RGB', '前景掩码', '清理后掩码',
              '最终边缘', '灰度形态学', '掩码内边界')
PARAMETERS = {
    'gaussian_strength': ('高斯强度 · Q8', None),
    'histogram_strength': ('直方图强度 · Q8', None),
    'foreground_low': ('前景低阈值', None), 'foreground_high': ('前景高阈值', None),
    'foreground_mode': ('分割方式', ('≥ 低阈值', '＜ 低阈值', '阈值区间')),
    'foreground_invert': ('分割反相', ('关闭', '开启')),
    'binary_operation': ('掩码操作', ('直通', '腐蚀', '膨胀', '开运算', '闭运算')),
    'binary_shape': ('掩码核', ('方形', '十字', '横向', '纵向')),
    'sobel_threshold': ('Sobel 阈值', None),
    'edge_neighbors': ('清理最少相邻点数', None),
    'edge_foreground_only': ('边缘范围', ('全图', '仅前景')),
    'overlay_rgb': ('描边颜色 · RRGGBB', None),
    'overlay_alpha': ('描边混合 · 四分比', None),
    'background_level': ('背景保留 · 四分比', None),
    'gray_operation': ('灰度操作', ('直通', '开运算', '闭运算', '白帽', '黑帽')),
    'gray_kernel5': ('灰度核', ('3×3', '5×5')),
    'gray_fusion': ('灰度融合 · 四分比', None),
    'gray_delta_clip': ('灰度改变量上限', None),
    'feature_luma': ('统计前景亮度阈值', None),
    'feature_highlight': ('统计高亮阈值', None),
}


class Adjustment:
    def __init__(self, nav, panel, quick, client, auto, schema, switch, change_zoom, change_fullscreen):
        self.client = client
        self.auto = auto
        self.schema = schema
        self.switch = switch
        self.enabled = False
        self.inputs = {}
        self.fields = {}
        self.labels = {}
        self.effective = {}
        self.observed = {}
        self.refreshing = False
        self.dirty = set()
        self.submitted = set()
        self.auto_started = False
        self.notice = ''
        self.identity = None
        self.pages = {}
        self.quick_columns = []
        navbar = Gtk.Box(spacing=4, homogeneous=True)
        navbar.get_style_context().add_class('vision')
        nav.add(navbar)
        self.nav_buttons = {}
        for active, caption in ((False, '显示页'), (True, '调整页')):
            button = self.button(caption, lambda value=active: self.show(value))
            navbar.pack_start(button, True, True, 0)
            self.nav_buttons[active] = button
        self.zoom_button = self.button('缩放 1×', change_zoom)
        navbar.pack_start(self.zoom_button, True, True, 0)
        self.fullscreen_button = self.button('画面全屏', change_fullscreen)
        navbar.pack_start(self.fullscreen_button, True, True, 0)

        shell = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        shell.get_style_context().add_class('vision')
        shell.get_style_context().add_class('panel')
        panel.add(shell)
        shell.pack_start(self.label('画面调整', 'heading'), False, False, 0)
        self.summary = self.label('等待板端发布实时参数', 'hint')
        shell.pack_start(self.summary, False, False, 0)

        tabs = Gtk.Box(spacing=4, homogeneous=True)
        shell.pack_start(tabs, False, False, 0)
        self.tabs = {}
        for key, name in (('isp', '相机与 ISP'), ('stages', '处理开关'), ('params', '处理参数')):
            button = self.button(name, lambda value=key: self.select(value))
            tabs.pack_start(button, True, True, 0)
            self.tabs[key] = button

        self.stack = Gtk.Stack()
        shell.pack_start(self.stack, True, True, 0)
        for key in self.tabs:
            scroll = Gtk.ScrolledWindow()
            scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
            scroll.set_overlay_scrolling(False)
            scroll.set_min_content_height(130)
            frame = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
            frame.set_margin_end(7)
            scroll.add(frame)
            self.stack.add_named(scroll, key)
            self.pages[key] = frame

        self.feedback = self.label('', 'hint')
        self.feedback.set_line_wrap(True)
        self.feedback.set_max_width_chars(44)
        shell.pack_start(self.feedback, False, False, 0)
        footer = Gtk.Box(spacing=8)
        shell.pack_start(footer, False, False, 0)
        self.apply_button = self.button('应用修改', self.apply)
        self.apply_button.get_style_context().add_class('primary')
        footer.pack_end(self.apply_button, False, False, 0)
        self.reset_button = self.button('撤销修改', self.reset)
        footer.pack_end(self.reset_button, False, False, 0)
        self.read_button = self.button('读取传感器', self.readback)
        footer.pack_start(self.read_button, False, False, 0)

        panel.connect('key-press-event', self.on_key)
        if quick is not None:
            common = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=9)
            common.get_style_context().add_class('vision')
            common.get_style_context().add_class('panel')
            quick.add(common)
            common.pack_start(self.label('常用参数', 'heading'), False, False, 0)
            columns = Gtk.Box(spacing=22, homogeneous=True)
            common.pack_start(columns, True, True, 0)
            for _ in range(2):
                column = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
                columns.pack_start(column, True, True, 0)
                self.quick_columns.append(column)
            quick.connect('key-press-event', self.on_key)
        for key, (caption, low, high, step) in ISP_FIELDS.items():
            if key == 'analog_ceiling_db':
                self.section(self.pages['isp'], '自动调节')
                self.add('isp', 'automatic', '连续调节', choices=('暂停', '运行'))
                for mode, label in AUTO_MODES.items():
                    self.add('isp', 'auto:' + mode, label, choices=('手动', '自动'))
            self.add('isp', key, caption, limits=(low, high, step))
        actions = Gtk.Box(spacing=5)
        self.pages['isp'].pack_start(actions, False, False, 4)
        self.auto_buttons = []
        for kind, caption in (('auto_exposure', '自动曝光一次'), ('auto_adjust', '自动调光与白平衡')):
            button = self.button(caption, lambda value=kind: self.start_auto(value))
            actions.pack_start(button, True, True, 0)
            self.auto_buttons.append(button)
        self.stop_button = self.button('停止自动调节任务', self.auto.cancel)
        self.stop_button.set_no_show_all(True)
        self.pages['isp'].pack_start(self.stop_button, False, False, 0)
        self.add('stages', 'view', '预览内容', choices=VIEW_NAMES)
        self.section(self.pages['stages'], '处理模块')
        for key in schema.FLAGS:
            self.add('stages', 'stage:' + key, STAGES[key], check=True)
        for key in schema.FIELDS:
            caption, choices = PARAMETERS[key]
            maximum = schema.FIELDS[key][3]
            self.add('params', 'param:' + key, caption, limits=(0, maximum, 1), choices=choices)
        self.select('isp')
        self.show(False)

    @staticmethod
    def button(caption, handler):
        button = Gtk.Button(label=caption)
        button.connect('clicked', lambda _: handler())
        return button

    @staticmethod
    def label(text, css_class=None):
        label = Gtk.Label(label=text, xalign=0)
        if css_class:
            label.get_style_context().add_class(css_class)
        return label

    def section(self, parent, text):
        parent.pack_start(self.label(text, 'section'), False, False, 0)

    def add(self, page, key, caption, limits=None, choices=None, check=False):
        row = Gtk.Box(spacing=6)
        parent = self.pages[page]
        if self.quick_columns and page == 'isp' and key in QUICK_ISP_COLUMNS:
            parent = self.quick_columns[QUICK_ISP_COLUMNS[key]]
        parent.pack_start(row, False, False, 0)
        row.pack_start(self.label(caption), True, True, 0)
        if check:
            widget = Gtk.CheckButton()
        elif choices:
            widget = Gtk.ComboBoxText()
            for choice in choices:
                widget.append_text(choice)
        elif key == 'param:overlay_rgb':
            widget = Gtk.Entry(width_chars=9, max_length=7)
        else:
            low, high, step = limits
            widget = Gtk.SpinButton.new_with_range(low, high, step)
            widget.set_width_chars(7)
            widget.set_update_policy(Gtk.SpinButtonUpdatePolicy.IF_VALID)
        row.pack_end(widget, False, False, 0)
        if check:
            self.effective[key] = self.label('', 'hint')
            row.pack_end(self.effective[key], False, False, 4)
        self.labels[key] = caption
        self.fields[key] = limits
        self.inputs[key] = widget
        widget.connect('toggled' if check else 'changed', lambda _: self.on_edit(key))

    def field_value(self, key):
        widget = self.inputs[key]
        if isinstance(widget, (Gtk.CheckButton, Gtk.ComboBoxText)):
            return widget.get_active()
        return widget.get_text()

    def on_edit(self, key):
        if self.refreshing or key not in self.observed:
            return
        if self.field_value(key) == self.observed[key]:
            self.dirty.discard(key)
        else:
            self.dirty.add(key)
        self.notice = ''
        self.refresh_buttons()

    def select(self, key):
        self.stack.set_visible_child_name(key)
        for name, button in self.tabs.items():
            context = button.get_style_context()
            (context.add_class if name == key else context.remove_class)('selected')

    def on_key(self, _, event):
        if event.keyval == Gdk.KEY_Escape:
            self.show(False)
            return True
        return False

    def show(self, active):
        if not active and (self.client.pending or self.auto.pending):
            self.notice = '相机正在应用参数，等待读回后可切换页面'
            self.refresh_buttons()
            return
        self.enabled = active
        for value, button in self.nav_buttons.items():
            context = button.get_style_context()
            (context.add_class if value == active else context.remove_class)('selected')
        self.switch(active)
        if active:
            self.update()

    def preview_buttons(self, zoom, fullscreen):
        self.zoom_button.set_label(f'缩放 {zoom:g}×')
        self.fullscreen_button.set_label('退出全屏' if fullscreen else '画面全屏')
        for button in self.nav_buttons.values():
            button.set_visible(not fullscreen)

    def refresh_buttons(self):
        ready = bool(self.client.status) and not self.auto.pending and not self.client.pending and not self.client.status.get('busy')
        self.apply_button.set_sensitive(bool(ready and self.dirty))
        self.read_button.set_sensitive(ready)
        self.reset_button.set_sensitive(bool(not self.auto.pending and not self.client.pending and self.dirty))
        for button in self.auto_buttons:
            button.set_sensitive(ready and not self.dirty)
        self.stop_button.set_visible(self.auto.pending)
        message = self.notice or (self.auto.message if self.auto_started else self.client.message)
        if not message and self.client.status:
            message = '参数已同步'
        if self.dirty and not self.notice and not self.client.pending and not self.submitted:
            message = f'{len(self.dirty)} 项待应用 · {message}'
        self.feedback.set_text(message)

    def update(self):
        status = self.client.poll()
        if not self.auto.pending and not self.client.pending and self.notice == '相机正在应用参数，等待读回后可切换页面':
            self.notice = ''
        if self.submitted and not self.client.pending:
            if self.client.last_ok:
                self.dirty.difference_update(self.submitted)
            self.submitted.clear()
            self.notice = ''
            for widget in self.inputs.values():
                widget.set_sensitive(True)
        if status and self.enabled:
            session = status['identity']['runtime_session_id']
            if session != self.identity:
                self.dirty.clear()
                self.identity = session
            self.sync(status)
        elif not status:
            self.summary.set_text('等待板端发布实时参数')
        self.refresh_buttons()
        return GLib.SOURCE_CONTINUE

    def sync(self, status):
        sensor = status['sensor']
        isp = status['isp']
        automatic = status['automatic']
        config = automatic['config']
        process = status.get('processing_controls')
        values = {key: isp['config'].get(key) for key in ('rg', 'bg', 'brightness', 'contrast')}
        gain_code = sensor.get('gain_code')
        values.update(gain_db=gain_code * 0.3 if gain_code is not None else None,
                      digital_gain=(automatic.get('enhancement') or {}).get('gain_q8'),
                      analog_ceiling_db=config['analog_ceiling_db'], digital_ceiling=config['digital_ceiling'],
                      automatic=int(not config['frozen']))
        if sensor.get('exposure_us') is not None:
            values['exposure_us'] = round(sensor['exposure_us'])
        values.update(('auto:' + key, int(config[key])) for key in AUTO_MODES)
        if process:
            requested, effective = process['requested'], process['effective']
            values['view'] = requested['view']
            values.update(('stage:' + key, val) for key, val in requested['enabled'].items())
            values.update(('param:' + key, val) for key, val in requested['parameters'].items())
            for key, label in self.effective.items():
                flag = effective['effective_enabled'][key[6:]]
                label.set_text(('生效' if flag else '关闭') if process['source_valid'] else '待读回')
                if not self.submitted:
                    supported = bool(process['supported_mask'] & (1 << self.schema.FLAGS.index(key[6:])))
                    self.inputs[key].set_sensitive(supported)
        self.refreshing = True
        for key, value in values.items():
            if value is None:
                continue
            widget = self.inputs[key]
            if isinstance(widget, (Gtk.CheckButton, Gtk.ComboBoxText)):
                display = value
            elif key == 'param:overlay_rgb':
                display = f'{value:06X}'
            elif key in ('gain_db', 'analog_ceiling_db', 'digital_ceiling'):
                display = f'{value:.1f}'
            else:
                display = str(value)
            self.observed[key] = display
            if key not in self.dirty and self.field_value(key) != display:
                if isinstance(widget, (Gtk.CheckButton, Gtk.ComboBoxText)):
                    widget.set_active(display)
                elif isinstance(widget, Gtk.SpinButton):
                    widget.set_value(value)
                else:
                    widget.set_text(display)
        self.refreshing = False
        exposure = sensor.get('exposure_us')
        exposure_label = f'{exposure:.1f} μs' if exposure is not None else '待读回'
        line = f'曝光 {exposure_label}   ·   自动调节 {"暂停" if config["frozen"] else "运行"}'
        if isp['wb_auto']:
            line += '\nFPGA 自动白平衡运行中，R/B 显示保留的手动值'
        if process:
            if process['fault']:
                line += '   ·   处理提交异常'
            elif process['pending']:
                line += '   ·   等待帧边界'
            elif process['source_valid']:
                line += f'\n处理版本 {process["effective_revision"]}   ·   源帧 {process["effective_source_frame_id"]}'
        self.summary.set_text(line)

    def readback(self):
        self.notice = ''
        self.auto_started = False
        self.client.submit({'operation': 'readback', 'values': {}})
        self.refresh_buttons()

    def reset(self):
        self.dirty.clear()
        self.notice = ''
        self.update()

    def start_auto(self, kind):
        if self.auto.start(kind):
            self.auto_started = True
            self.notice = ''
            self.refresh_buttons()

    def apply(self):
        self.notice = ''
        self.auto_started = False
        status = self.client.status
        if not status:
            return
        try:
            edits = {}
            for key in self.dirty:
                raw = self.field_value(key)
                if isinstance(raw, (bool, int)):
                    value = raw
                else:
                    low, high, _ = self.fields[key]
                    if key == 'param:overlay_rgb':
                        value = int(raw.strip().removeprefix('#'), 16)
                    elif key in ('gain_db', 'analog_ceiling_db', 'digital_ceiling'):
                        value = float(raw)
                    else:
                        value = int(raw)
                    if not low <= value <= high:
                        raise ValueError(f'{self.labels[key]}范围为 {low}–{high}')
                edits[key] = value

            automatic = {key[5:]: bool(value) for key, value in edits.items() if key.startswith('auto:')}
            for key in ('analog_ceiling_db', 'digital_ceiling'):
                if key in edits:
                    automatic[key] = edits[key]
            if 'automatic' in edits:
                automatic['frozen'] = not edits['automatic']
            isp = {key: edits[key] for key in ('digital_gain', 'brightness', 'contrast', 'rg', 'bg') if key in edits}
            for field, mode in (('digital_gain', 'digital_gain'), ('brightness', 'brightness'),
                                ('contrast', 'contrast'), ('gain_db', 'analog_gain')):
                if field in edits:
                    if automatic.get(mode):
                        raise ValueError(f'{AUTO_MODES[mode]}的手动数值与自动模式一起修改，请先选定调节方式')
                    automatic[mode] = False
            if 'rg' in isp or 'bg' in isp:
                isp['wb_auto'] = False

            processing = {}
            if 'view' in edits:
                processing['view'] = edits['view']
            for name, prefix in (('enabled', 'stage:'), ('parameters', 'param:')):
                fields = {key[len(prefix):]: value for key, value in edits.items() if key.startswith(prefix)}
                if fields:
                    processing[name] = fields
            if processing:
                process = status.get('processing_controls')
                if not process:
                    raise ValueError('当前板端没有发布公共处理接口')
                if process['pending']:
                    raise ValueError('上一组处理参数正在等待帧边界')
                if not automatic.get('frozen', status['automatic']['config']['frozen']):
                    raise ValueError('把「连续调节」设为暂停，再应用处理参数')
                self.schema.apply_patch(process['requested']['words'], processing)

            requests = []
            if automatic:
                isp['auto'] = automatic
            if isp:
                requests.append(dict(operation='isp', isp=isp))
            if 'exposure_us' in edits or 'gain_db' in edits:
                if not status['capabilities']['sensor_hotupdate']:
                    raise ValueError('当前板端没有启用传感器热更新')
                values = {'exposure_us': edits.get('exposure_us', status['sensor'].get('exposure_us'))}
                if values['exposure_us'] is None:
                    raise ValueError('请先读取传感器')
                if 'gain_db' in edits:
                    values['gain_code'] = round(edits['gain_db'] / .3)
                requests.append(dict(operation='exposure', values=values))
            if processing:
                requests.append(dict(operation='process', values=processing))
        except ValueError as error:
            self.notice = str(error) if not str(error).startswith(('invalid literal', 'could not convert')) else '请检查参数是否填写为有效数值'
        else:
            if requests and self.client.submit(*requests):
                self.submitted = set(self.dirty)
                for widget in self.inputs.values():
                    widget.set_sensitive(False)
        self.refresh_buttons()


def run(runtime, schema):
    style = Gtk.CssProvider()
    style.load_from_data(STYLE)
    Gtk.StyleContext.add_provider_for_screen(Gdk.Screen.get_default(), style, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
    x11 = PreviewWindow()
    while not (preview := x11.find()):
        time.sleep(1)
    xid = preview[0]
    zoom_levels = (1, 1.5, 2)
    zoom_index = 0
    picture_fullscreen = False

    def positions(geometry):
        _, width, height = geometry
        panel_top = 82 if width < 1100 else 155
        nav_box = (width - 244, 23, 224, 42) if picture_fullscreen else (width - 446, 23, 426, 42)
        return nav_box, (width - 436, panel_top, 416, height - panel_top - 18)

    nav_box, panel_box = positions(preview)
    nav_id = x11.attach(xid, *nav_box)
    panel_id = x11.attach(xid, *panel_box, visible=False)
    nav = Gtk.Window(type=Gtk.WindowType.TOPLEVEL, title='RK HDMI 页面导航')
    panel = Gtk.Window(type=Gtk.WindowType.TOPLEVEL, title='RK HDMI 画面调整')
    windows = [(nav, nav_id, nav_box), (panel, panel_id, panel_box)]
    quick = quick_id = None
    if preview[1:] == (1024, 600):
        quick_box = (24, 82, 544, 162)
        quick_id = x11.attach(xid, *quick_box, visible=False)
        quick = Gtk.Window(type=Gtk.WindowType.TOPLEVEL, title='RK HDMI 常用参数')
        windows.append((quick, quick_id, quick_box))
    state_dir = Path('/run/rk-hdmi-adjust')
    marker = state_dir / 'adjustment-active'
    preview_controls = state_dir / 'preview-controls.json'
    timer = None
    client = CameraControls(runtime / 'camera_console')
    auto = AutoAdjustment(runtime)

    def switch(active):
        nonlocal timer
        if active and not picture_fullscreen:
            x11.place(panel_id, *panel_box)
            if quick_id is not None:
                x11.place(quick_id, *quick_box)
            marker.touch()
            x11.focus(panel.get_window().get_xid())
            if timer is None:
                timer = GLib.timeout_add(250, app.update)
        else:
            if timer is not None:
                GLib.source_remove(timer)
                timer = None
            marker.unlink(missing_ok=True)
            x11.hide(panel_id)
            if quick_id is not None:
                x11.hide(quick_id)
            x11.focus(xid)

    def publish_view():
        zoom = zoom_levels[zoom_index]
        if zoom == 1 and not picture_fullscreen:
            preview_controls.unlink(missing_ok=True)
        else:
            temporary = preview_controls.with_suffix('.tmp')
            temporary.write_text(json.dumps({'zoom': zoom, 'fullscreen': picture_fullscreen}), encoding='utf-8')
            temporary.replace(preview_controls)
        app.preview_buttons(zoom, picture_fullscreen)

    def change_zoom():
        nonlocal zoom_index
        zoom_index = (zoom_index + 1) % len(zoom_levels)
        publish_view()

    def change_fullscreen():
        nonlocal picture_fullscreen, nav_box, panel_box
        if not picture_fullscreen and (client.pending or auto.pending):
            app.notice = '相机正在应用参数，等待读回后可切换页面'
            app.refresh_buttons()
            return
        picture_fullscreen = not picture_fullscreen
        publish_view()
        nav_box, panel_box = positions(preview)
        x11.place(nav_id, *nav_box)
        nav.resize(*nav_box[2:])
        switch(app.enabled)
        if picture_fullscreen:
            x11.focus(nav.get_window().get_xid())
        elif app.enabled:
            app.update()

    def nav_key(_, event):
        if event.keyval == Gdk.KEY_Escape and picture_fullscreen:
            change_fullscreen()
            return True
        return False

    app = Adjustment(nav, panel, quick, client, auto, schema, switch, change_zoom, change_fullscreen)
    marker.unlink(missing_ok=True)
    preview_controls.unlink(missing_ok=True)
    nav.connect('key-press-event', nav_key)
    for window, container, box in windows:
        window.set_decorated(False)
        window.set_default_size(*box[2:])
        window.realize()
        gdk_window = window.get_window()
        gdk_window.set_override_redirect(True)
        window.get_display().sync()
        if not x11.reparent(gdk_window.get_xid(), container):
            raise RuntimeError('HDMI 预览窗口已关闭')
        window.show_all()
    if quick is not None:
        def focus_quick(window, _event):
            x11.focus(window.get_window().get_xid())
            return False
        quick.connect('button-press-event', focus_quick)

    def follow_preview():
        nonlocal preview, nav_box, panel_box
        current = x11.find()
        if not current or current[0] != xid:
            Gtk.main_quit()
            return GLib.SOURCE_REMOVE
        if current != preview:
            preview = current
            nav_box, panel_box = positions(preview)
            x11.place(nav_id, *nav_box)
            x11.place(panel_id, *panel_box, visible=app.enabled and not picture_fullscreen)
            panel.resize(*panel_box[2:])
        if app.enabled and not picture_fullscreen:
            marker.touch()
        return GLib.SOURCE_CONTINUE

    GLib.timeout_add(500, follow_preview)
    try:
        Gtk.main()
    finally:
        auto.stop()
        marker.unlink(missing_ok=True)
        preview_controls.unlink(missing_ok=True)
        x11.close()
    raise RuntimeError('HDMI 预览窗口已关闭，等待重新连接')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--runtime', type=Path, required=True)
    args = parser.parse_args()
    runtime = args.runtime.resolve()
    sys.path.insert(0, str(runtime))
    import process_controls

    os.nice(7)
    os.sched_setaffinity(0, os.sched_getaffinity(0) & {0, 1, 2, 3})
    run(runtime, process_controls)


if __name__ == '__main__':
    main()
