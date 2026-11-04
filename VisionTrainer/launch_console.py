from pathlib import Path
import argparse
import ctypes
from ctypes import wintypes
import http.cookiejar
import json
import os
import subprocess
import time
import urllib.request

ROOT = Path(__file__).resolve().parent
ENTRY = ROOT / "console_app.py"
PYTHON = ROOT / ".venv-gpu" / "Scripts" / "python.exe"
ADDRESS = "http://127.0.0.1:18763"
LOGS = ROOT / "console_logs"
OPENER = urllib.request.build_opener(
    urllib.request.ProxyHandler({}),
    urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()),
)


def request(path):
    with OPENER.open(ADDRESS + path, timeout=3) as response:
        return json.load(response)


def health():
    try:
        value = request("/api/health")
    except (OSError, ValueError):
        return None
    if value.get("app") != "native-vision-console":
        raise RuntimeError("18763 端口被其他服务占用，未停止任何程序。")
    return value


def powershell(script):
    env = os.environ.copy()
    env["VISION_CONSOLE_ENTRY"] = str(ENTRY)
    result = subprocess.run(
        [
            "powershell.exe",
            "-NoProfile",
            "-NonInteractive",
            "-Command",
            "$ErrorActionPreference='Stop'; " + script,
        ],
        env=env,
        capture_output=True,
        creationflags=subprocess.CREATE_NO_WINDOW,
        timeout=20,
    )
    if result.returncode:
        raise RuntimeError("无法安全识别或停止本机控制台进程，未继续启动。")
    return result.stdout.decode("utf-8-sig").strip()


def stop_server(status):
    active = status.get("active_jobs")
    if active is None:
        request("/api/bootstrap")
        active = sum(
            j["state"] in ("queued", "running") for j in request("/api/state")["jobs"]
        )
    if active:
        raise RuntimeError(
            "控制台仍有任务执行中，请先在页面停止或等待任务完成，再重启。"
        )

    powershell(r"""
        $listeners = @(Get-NetTCPConnection -LocalPort 18763 -State Listen -ErrorAction SilentlyContinue)
        $owners = @($listeners | Select-Object -ExpandProperty OwningProcess -Unique)
        if ($owners.Count -ne 1) { throw 'Ambiguous listener' }
        $owner = Get-CimInstance Win32_Process -Filter ('ProcessId=' + $owners[0])
        $entryPattern = '(?i)(?:^|\s)"?' + [regex]::Escape($env:VISION_CONSOLE_ENTRY) + '"?(?=\s|$)'
        if ($owner.Name -notin @('python.exe','pythonw.exe') -or
            $owner.CommandLine -notmatch $entryPattern) {
            throw 'Unrecognized console process'
        }
        Stop-Process -Id $owner.ProcessId -ErrorAction Stop
        Wait-Process -Id $owner.ProcessId -Timeout 10 -ErrorAction SilentlyContinue
    """)


def select_flow(explicit):
    flows = []
    for path in (ROOT / "console_data").glob("*/flow.json"):
        try:
            flow = json.loads(path.read_text(encoding="utf-8"))
            identity = flow["id"]
            if len(identity) == 32 and all(c in "0123456789abcdef" for c in identity):
                flows.append(flow)
        except (OSError, ValueError, KeyError):
            continue
    if explicit:
        if explicit not in {f["id"] for f in flows}:
            raise RuntimeError("指定流程不存在；没有创建或覆盖任何历史流程。")
        return explicit
    return max(flows, key=lambda f: f.get("updated_at", ""), default={}).get("id")


def main():
    parser = argparse.ArgumentParser(description="打开原点控制台，恢复最近保存的流程")
    parser.add_argument("--restart", action="store_true", help="重启空闲的本机控制台")
    parser.add_argument("--flow", help="打开指定历史流程")
    args = parser.parse_args()
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateMutexW.argtypes = [wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR]
    kernel.CreateMutexW.restype = wintypes.HANDLE
    kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel.WaitForSingleObject.restype = wintypes.DWORD
    kernel.ReleaseMutex.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    mutex = kernel.CreateMutexW(None, False, r"Local\NativeVisionConsoleLauncher18763")
    if not mutex:
        raise ctypes.WinError(ctypes.get_last_error())
    acquired = False
    try:
        acquired = kernel.WaitForSingleObject(mutex, 15000) in (0, 0x80)
        if not acquired:
            raise RuntimeError("另一个启动器正在启动，请稍后再打开。")
        if not PYTHON.is_file() or not ENTRY.is_file():
            raise RuntimeError("缺少控制台文件或 .venv-gpu 环境：" + str(ROOT))
        flow = select_flow(args.flow)
        status = health()
        if status and args.restart:
            stop_server(status)
            status = None
        if not status:
            LOGS.mkdir(exist_ok=True)
            with (LOGS / "server.stdout.log").open("ab") as out, (
                LOGS / "server.stderr.log"
            ).open("ab") as err:
                process = subprocess.Popen(
                    [str(PYTHON), "-u", str(ENTRY)],
                    cwd=str(ROOT),
                    stdin=subprocess.DEVNULL,
                    stdout=out,
                    stderr=err,
                    creationflags=subprocess.CREATE_NO_WINDOW
                    | subprocess.CREATE_NEW_PROCESS_GROUP,
                    close_fds=True,
                )
            (LOGS / "server.pid").write_text(str(process.pid), encoding="ascii")
            deadline = time.monotonic() + 35
            while time.monotonic() < deadline:
                if process.poll() is not None:
                    raise RuntimeError(
                        "控制台启动失败，请查看：" + str(LOGS / "server.stderr.log")
                    )
                if health():
                    break
                time.sleep(0.4)
            else:
                raise RuntimeError("本机服务尚未就绪，请查看：" + str(LOGS))
        url = ADDRESS + "/?connect=manual" + ("&flow=" + flow if flow else "")
        os.startfile(url)
        print("Console opened: " + url)
    finally:
        if acquired:
            kernel.ReleaseMutex(mutex)
        kernel.CloseHandle(mutex)


def run():
    try:
        main()
    except Exception as exc:
        LOGS.mkdir(exist_ok=True)
        (LOGS / "launcher-error.txt").write_text(str(exc), encoding="utf-8")
        ctypes.windll.user32.MessageBoxW(None, str(exc), "原点控制台启动失败", 0x10)
        raise SystemExit(1)


if __name__ == "__main__":
    run()
