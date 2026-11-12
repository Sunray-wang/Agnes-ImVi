# -*- coding: utf-8 -*-
"""
Agnes 生成工作台 — FastAPI 后端

职责：
  1. 持有 API Key（.env），代理 Agnes 图像接口（文生图 / 图生图 / 多图合成）
  2. 代理 Agnes 视频接口（文生视频 / 首尾帧 / 图生视频 / 音频参考，异步任务轮询）
  3. 结果 URL 秒级失效，故生成成功后立即下载落盘到 outputs/
  4. 托管 static/ 单页前端

启动：python main.py  （默认 http://127.0.0.1:8000）
"""
import json
import mimetypes
import os
import threading
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path
from urllib.parse import quote

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

ROOT = Path(__file__).resolve().parent
STATIC_DIR = ROOT / "static"
OUTPUTS_DIR = ROOT / "outputs"

load_dotenv(ROOT / ".env")

AGNES_BASE = os.getenv("AGNES_BASE_URL", "https://apihub.agnes-ai.com/v1").rstrip("/")
AGNES_KEY = os.getenv("AGNES_API_KEY", "").strip()
MODEL_IMAGE = "agnes-image-2.5-flash"
MODEL_VIDEO = "agnes-video-2.5-flash"
VIDEO_POLL_BASE = "https://apihub.agnes-ai.com/agnesapi"
MEDIA_HOSTS = ("platform-outputs.agnes-ai.space", "storage.googleapis.com", "apihub.agnes-ai.com")
MEDIA_TIMEOUT = 120
GEN_TIMEOUT = 300
VIDEO_TIMEOUT = 45          # 创建请求本身应秒回，挂起就快速失败重试
VIDEO_MAX_WAIT = 600        # 单个视频任务最长轮询 10 分钟
VIDEO_POLL_INTERVAL = 3     # 后端线程内轮询间隔
VIDEO_RETRY_WAITS = (30, 60, 90)  # 创建任务遇 503 拥堵 / 429 限速的等待秒数

OUTPUTS_DIR.mkdir(exist_ok=True)

# 视频固定 720P，各 aspect_ratio -> 输出像素（与 API 文档一致）
VIDEO_DIMS = {
    "21:9": "1680x720", "16:9": "1280x720", "4:3": "960x720",
    "1:1": "720x720", "3:4": "720x960", "9:16": "720x1280",
}
VIDEO_RATIOS = tuple(VIDEO_DIMS.keys())
VIDEO_SECONDS = tuple(str(s) for s in range(4, 13))  # "4"–"12"，字符串

# size 档位 × ratio -> 预期输出像素（与 API 文档一致）
DIMS = {
    "1K": {"1:1": "1024x1024", "3:4": "864x1152", "4:3": "1152x864", "16:9": "1312x736",
           "9:16": "736x1312", "2:3": "832x1248", "3:2": "1248x832", "21:9": "1568x672"},
    "2K": {"1:1": "2048x2048", "3:4": "1728x2304", "4:3": "2304x1728", "16:9": "2624x1472",
           "9:16": "1472x2624", "2:3": "1664x2496", "3:2": "2496x1664", "21:9": "3136x1344"},
    "3K": {"1:1": "3072x3072", "3:4": "2592x3456", "4:3": "3456x2592", "16:9": "3936x2208",
           "9:16": "2208x3936", "2:3": "2496x3744", "3:2": "3744x2496", "21:9": "4704x2016"},
    "4K": {"1:1": "4096x4096", "3:4": "3456x4608", "4:3": "4608x3456", "16:9": "5248x2944",
           "9:16": "2944x5248", "2:3": "3328x4992", "3:2": "4992x3328", "21:9": "6272x2688"},
}
SIZES = tuple(DIMS.keys())
RATIOS = tuple(DIMS["1K"].keys())

app = FastAPI(title="Agnes 生成工作台")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


# ---------- 请求模型 ----------

class TextToImageBody(BaseModel):
    prompt: str
    size: str = "1K"
    ratio: str = "1:1"


class ImageToImageBody(BaseModel):
    prompt: str
    image: str                      # 公网 HTTPS URL 或 Data URI Base64
    size: str = "1K"
    ratio: str = "1:1"


class MultiImageBody(BaseModel):
    prompt: str
    images: list[str]               # 2 张及以上
    size: str = "1K"
    ratio: str = "1:1"


class VideoBaseBody(BaseModel):
    prompt: str
    seconds: str = "4"              # 字符串 "4"–"12"
    aspect_ratio: str = "16:9"


class TextToVideoBody(VideoBaseBody):
    pass


class KeyframeVideoBody(VideoBaseBody):
    first_frame: str
    last_frame: str = ""            # 可选


class ReferenceVideoBody(VideoBaseBody):
    images: list[str] = []          # ≤5
    audios: list[str] = []          # ≤3


# ---------- 工具函数 ----------

def _check_key():
    if not AGNES_KEY:
        raise HTTPException(500, "服务端未配置 AGNES_API_KEY，请在项目根目录 .env 中填写")


def _check_params(size: str, ratio: str):
    if size not in DIMS:
        raise HTTPException(400, f"不支持的尺寸档位 size={size!r}，可选：{', '.join(SIZES)}")
    if ratio not in DIMS[size]:
        raise HTTPException(400, f"不支持的宽高比 ratio={ratio!r}，可选：{', '.join(RATIOS)}")


def _check_image(image: str, field: str):
    """输入图只接受 Data URI 或公网 http(s) URL"""
    if not isinstance(image, str) or not image.strip():
        raise HTTPException(400, f"{field} 不能为空")
    image = image.strip()
    if image.startswith("data:image/"):
        return image
    if image.startswith("http://") or image.startswith("https://"):
        return image
    raise HTTPException(400, f"{field} 需为公网图片 URL 或 Data URI Base64（以 data:image/ 开头）")


def _agnes_generate(payload: dict) -> dict:
    """调用 Agnes 图像生成接口，返回响应 JSON。
    服务端队列拥堵（503 queue is full）时自动退避重试，最多额外等待约 140 秒。"""
    _check_key()
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    retry_waits = (20, 45, 75)  # 第 1/2/3 次拥堵后的等待秒数
    last_detail = ""
    for attempt in range(len(retry_waits) + 1):
        req = urllib.request.Request(AGNES_BASE + "/images/generations", data=body, method="POST")
        req.add_header("Authorization", "Bearer " + AGNES_KEY)
        req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req, timeout=GEN_TIMEOUT) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            raw = e.read().decode("utf-8", "replace")
            try:
                raw = json.loads(raw)
            except Exception:
                pass
            last_detail = json.dumps(raw, ensure_ascii=False)
            # 队列拥堵（503）或免费账号限速（429）：等待后重试
            retryable = (e.code == 503 and "queue" in last_detail.lower()) or e.code == 429
            if retryable and attempt < len(retry_waits):
                wait = retry_waits[attempt]
                reason = "队列拥堵" if e.code == 503 else "免费账号限速(429)"
                print(f"[ Agnes 图像{reason} ] 第 {attempt + 1} 次重试，等待 {wait}s ...", flush=True)
                time.sleep(wait)
                continue
            if e.code == 503:
                raise HTTPException(503, "Agnes 服务繁忙（队列已满），已自动重试 "
                                        f"{attempt} 次仍失败，请稍后再试")
            if e.code == 429:
                raise HTTPException(429, "已触发 Agnes 免费账号速率限制，请稍等几分钟再试")
            raise HTTPException(502, f"Agnes 接口返回 HTTP {e.code}：{last_detail[:400]}")
        except Exception as e:
            raise HTTPException(502, f"Agnes 接口调用失败：{e!r}")
    raise HTTPException(503, "Agnes 服务持续拥堵，请稍后再试")


def _download(url: str) -> tuple[bytes, str]:
    """下载结果图片/视频，返回 (字节, content-type)"""
    if not url.startswith("https://"):
        raise HTTPException(502, f"结果 URL 非 HTTPS，拒绝下载：{url[:80]}")
    # 信任域名白名单：已知 CDN + 兜底（含 agnes-ai / googleapis 的任意子域，视频结果域名未经实测）
    trusted = any(host in url for host in MEDIA_HOSTS) or "agnes-ai" in url or "googleapis" in url
    if not trusted:
        raise HTTPException(502, f"结果 URL 不在信任域名白名单：{url[:80]}")
    req = urllib.request.Request(url, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=MEDIA_TIMEOUT) as resp:
            return resp.read(), resp.headers.get("Content-Type", "image/png")
    except Exception as e:
        raise HTTPException(502, f"结果下载失败（URL 可能已过期）：{e!r}")


def _save(data: bytes, content_type: str, prefix: str) -> str:
    """落盘 outputs/，返回文件名"""
    if "jpeg" in content_type or "jpg" in content_type:
        ext = ".jpg"
    elif "mp4" in content_type or "video" in content_type:
        ext = ".mp4"
    elif "wav" in content_type or "audio" in content_type:
        ext = ".wav"
    else:
        ext = ".png"
    name = "%s_%s%s" % (prefix, time.strftime("%Y%m%d_%H%M%S"), ext)
    (OUTPUTS_DIR / name).write_bytes(data)
    return name


def _finish(resp: dict, size: str, ratio: str, t0: float, prefix: str) -> dict:
    """统一收尾：取 URL -> 立即下载落盘 -> 返回前端所需信息"""
    items = resp.get("data") or []
    url = (items[0] or {}).get("url") if items else None
    if not url:
        raise HTTPException(502, f"Agnes 响应中未找到图片 URL：{json.dumps(resp, ensure_ascii=False)[:300]}")
    data, ctype = _download(url)
    name = _save(data, ctype, prefix)
    return {
        "ok": True,
        "file": name,
        "src": f"/api/files/{name}",
        "bytes": len(data),
        "elapsed": round(time.time() - t0, 1),
        "expected": DIMS[size][ratio],
        "source_url": url,
    }


# ---------- 图像接口 ----------

@app.post("/api/image/text2img")
def text_to_image(body: TextToImageBody):
    """文生图"""
    _check_params(body.size, body.ratio)
    t0 = time.time()
    resp = _agnes_generate({
        "model": MODEL_IMAGE,
        "prompt": body.prompt,
        "size": body.size,
        "ratio": body.ratio,
        "extra_body": {"response_format": "url"},
    })
    return _finish(resp, body.size, body.ratio, t0, "t2i")


@app.post("/api/image/img2img")
def image_to_image(body: ImageToImageBody):
    """图生图（单张输入图）"""
    _check_params(body.size, body.ratio)
    image = _check_image(body.image, "image")
    t0 = time.time()
    resp = _agnes_generate({
        "model": MODEL_IMAGE,
        "prompt": body.prompt,
        "size": body.size,
        "ratio": body.ratio,
        "extra_body": {
            "image": [image],
            "response_format": "url",
        },
    })
    return _finish(resp, body.size, body.ratio, t0, "i2i")


@app.post("/api/image/multi")
def multi_image(body: MultiImageBody):
    """多图合成（2 张及以上参考图）"""
    _check_params(body.size, body.ratio)
    if len(body.images) < 2:
        raise HTTPException(400, "多图合成至少需要 2 张参考图")
    if len(body.images) > 6:
        raise HTTPException(400, "参考图最多 6 张")
    images = [_check_image(x, f"images[{i}]") for i, x in enumerate(body.images)]
    t0 = time.time()
    resp = _agnes_generate({
        "model": MODEL_IMAGE,
        "prompt": body.prompt,
        "size": body.size,
        "ratio": body.ratio,
        "extra_body": {
            "image": images,
            "response_format": "url",
        },
    })
    return _finish(resp, body.size, body.ratio, t0, "multi")


# ---------- 视频接口 ----------

def _check_video_params(seconds: str, aspect_ratio: str):
    if seconds not in VIDEO_SECONDS:
        raise HTTPException(400, f"视频时长 seconds={seconds!r} 非法，可选：{', '.join(VIDEO_SECONDS)}（字符串）")
    if aspect_ratio not in VIDEO_DIMS:
        raise HTTPException(400, f"不支持的宽高比 aspect_ratio={aspect_ratio!r}，可选：{', '.join(VIDEO_RATIOS)}")


def _check_media(value: str, field: str, kind: str) -> str:
    """视频输入媒体只接受 Data URI（data:image/ 或 data:audio/）或公网 http(s) URL"""
    if not isinstance(value, str) or not value.strip():
        raise HTTPException(400, f"{field} 不能为空")
    value = value.strip()
    prefix = "data:image/" if kind == "image" else "data:audio/"
    if value.startswith(prefix):
        return value
    if value.startswith("http://") or value.startswith("https://"):
        return value
    raise HTTPException(400, f"{field} 需为公网 URL 或 {prefix} 开头的 Data URI")


def _agnes_create_video(payload: dict, job: dict | None = None) -> str:
    """创建视频任务，返回 video_id。503 队列拥堵 / 429 限速时自动退避重试。
    job 非空时，把重试进度写进 job（供前端展示）。"""
    _check_key()
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    last_detail = ""
    for attempt in range(len(VIDEO_RETRY_WAITS) + 1):
        if job is not None:
            job["create_attempt"] = attempt
        req = urllib.request.Request(AGNES_BASE + "/videos", data=body, method="POST")
        req.add_header("Authorization", "Bearer " + AGNES_KEY)
        req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req, timeout=VIDEO_TIMEOUT) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                vid = data.get("video_id") or data.get("id") or data.get("task_id")
                if not vid:
                    raise HTTPException(502, f"Agnes 创建响应中未找到 video_id：{json.dumps(data, ensure_ascii=False)[:300]}")
                return str(vid)
        except urllib.error.HTTPError as e:
            raw = e.read().decode("utf-8", "replace")
            try:
                raw = json.loads(raw)
            except Exception:
                pass
            last_detail = json.dumps(raw, ensure_ascii=False)
            # 队列拥堵（503）或免费账号限速（429）：等待后重试
            retryable = (e.code == 503 and "queue" in last_detail.lower()) or e.code == 429
            if retryable and attempt < len(VIDEO_RETRY_WAITS):
                wait = VIDEO_RETRY_WAITS[attempt]
                reason = "队列拥堵" if e.code == 503 else "免费账号限速(429)"
                print(f"[ Agnes 视频{reason} ] 第 {attempt + 1} 次重试，等待 {wait}s ...", flush=True)
                time.sleep(wait)
                continue
            if e.code == 503:
                raise HTTPException(503, "Agnes 视频队列已满，已自动重试 "
                                        f"{attempt} 次仍失败，请稍后再试")
            if e.code == 429:
                raise HTTPException(429, "已触发 Agnes 免费账号速率限制，请稍等几分钟再试")
            raise HTTPException(502, f"Agnes 接口返回 HTTP {e.code}：{last_detail[:400]}")
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(502, f"Agnes 接口调用失败：{e!r}")
    raise HTTPException(503, "Agnes 视频服务持续拥堵，请稍后再试")


# job_id -> 任务状态字典（后台线程执行创建/轮询/落盘，前端轮询此表）
# status: creating（创建中，含拥堵重试）/ polling（已创建，轮询 Agnes）/ completed /
#         completed_download_failed / failed
_video_jobs: dict[str, dict] = {}


def _agnes_video_status(video_id: str) -> dict:
    """轮询一次 Agnes 任务状态"""
    url = f"{VIDEO_POLL_BASE}?video_id={quote(video_id)}&model_name={MODEL_VIDEO}"
    req = urllib.request.Request(url, method="GET")
    req.add_header("Authorization", "Bearer " + AGNES_KEY)
    try:
        with urllib.request.urlopen(req, timeout=VIDEO_TIMEOUT) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", "replace")
        try:
            raw = json.loads(raw)
        except Exception:
            pass
        raise HTTPException(502, f"Agnes 轮询接口返回 HTTP {e.code}：{json.dumps(raw, ensure_ascii=False)[:300]}")
    except Exception as e:
        raise HTTPException(502, f"Agnes 轮询接口调用失败：{e!r}")


def _run_video_job(job_id: str, payload: dict):
    """后台线程：创建任务（含重试）→ 轮询 → 完成后立即下载落盘"""
    job = _video_jobs[job_id]
    t0 = job["t0"]
    try:
        vid = _agnes_create_video(payload, job)
        job["video_id"] = vid
        job["status"] = "polling"
        print(f"[ 视频任务 {job_id[:8]} ] 已创建 video_id={vid}", flush=True)

        while time.time() - t0 < VIDEO_MAX_WAIT:
            resp = _agnes_video_status(vid)
            status = resp.get("status", "")
            job["progress"] = resp.get("progress", 0)
            job["raw_status"] = status
            if status == "completed":
                url = resp.get("url") or ""
                job["source_url"] = url
                if url:
                    try:
                        data, ctype = _download(url)
                        name = _save(data, "video/mp4", "v")
                        job["file"] = name
                        job["bytes"] = len(data)
                        job["status"] = "completed"
                        job["elapsed"] = round(time.time() - t0, 1)
                        print(f"[ 视频任务 {job_id[:8]} ] 完成并落盘 {name}", flush=True)
                    except HTTPException as e:
                        job["status"] = "completed_download_failed"
                        job["error"] = str(e.detail)
                        job["elapsed"] = round(time.time() - t0, 1)
                else:
                    job["status"] = "failed"
                    job["error"] = "任务完成但 Agnes 未返回结果 URL"
                return
            if status in ("failed", "cancelled"):
                job["status"] = "failed"
                job["error"] = resp.get("error") or resp.get("message") or status
                job["elapsed"] = round(time.time() - t0, 1)
                return
            time.sleep(VIDEO_POLL_INTERVAL)
        job["status"] = "failed"
        job["error"] = f"轮询超时（已等待 {VIDEO_MAX_WAIT} 秒）"
        job["elapsed"] = round(time.time() - t0, 1)
    except HTTPException as e:
        job["status"] = "failed"
        job["error"] = str(e.detail)
        job["elapsed"] = round(time.time() - t0, 1)
    except Exception as e:
        job["status"] = "failed"
        job["error"] = repr(e)
        job["elapsed"] = round(time.time() - t0, 1)


def _start_video_job(payload: dict, expected: str) -> dict:
    """立即返回 job_id，后台线程干活——创建重试不再阻塞请求"""
    _check_key()
    job_id = uuid.uuid4().hex
    _video_jobs[job_id] = {
        "status": "creating", "video_id": "", "progress": 0,
        "create_attempt": 0, "file": "", "bytes": 0, "source_url": "",
        "error": "", "elapsed": 0.0, "expected": expected,
        "t0": time.time(),
    }
    thread = threading.Thread(target=_run_video_job, args=(job_id, payload), daemon=True)
    thread.start()
    return {"ok": True, "job_id": job_id, "expected": expected}


@app.post("/api/video/text2video")
def text_to_video(body: TextToVideoBody):
    """文生视频（text 模式，禁止任何媒体字段）"""
    _check_video_params(body.seconds, body.aspect_ratio)
    if not body.prompt.strip():
        raise HTTPException(400, "提示词不能为空")
    return _start_video_job({
        "model": MODEL_VIDEO,
        "prompt": body.prompt,
        "size": "720P",
        "seconds": body.seconds,
        "aspect_ratio": body.aspect_ratio,
        "mode": "text",
    }, VIDEO_DIMS[body.aspect_ratio])


@app.post("/api/video/keyframe")
def keyframe_video(body: KeyframeVideoBody):
    """首尾帧生视频（keyframe 模式，首/尾帧至少一个）"""
    _check_video_params(body.seconds, body.aspect_ratio)
    if not body.prompt.strip():
        raise HTTPException(400, "提示词不能为空")
    payload = {
        "model": MODEL_VIDEO,
        "prompt": body.prompt,
        "size": "720P",
        "seconds": body.seconds,
        "aspect_ratio": body.aspect_ratio,
        "mode": "keyframe",
        "first_frame": _check_media(body.first_frame, "first_frame", "image"),
    }
    if body.last_frame.strip():
        payload["last_frame"] = _check_media(body.last_frame, "last_frame", "image")
    return _start_video_job(payload, VIDEO_DIMS[body.aspect_ratio])


@app.post("/api/video/reference")
def reference_video(body: ReferenceVideoBody):
    """图生视频 / 音频参考（reference 模式，images≤5、audios≤3，不支持 videos）"""
    _check_video_params(body.seconds, body.aspect_ratio)
    if not body.prompt.strip():
        raise HTTPException(400, "提示词不能为空")
    if not body.images and not body.audios:
        raise HTTPException(400, "reference 模式至少需要 1 张图片或 1 段音频")
    if len(body.images) > 5:
        raise HTTPException(400, "图片引用最多 5 张")
    if len(body.audios) > 3:
        raise HTTPException(400, "音频引用最多 3 段")
    payload = {
        "model": MODEL_VIDEO,
        "prompt": body.prompt,
        "size": "720P",
        "seconds": body.seconds,
        "aspect_ratio": body.aspect_ratio,
        "mode": "reference",
    }
    if body.images:
        payload["images"] = [_check_media(x, f"images[{i}]", "image") for i, x in enumerate(body.images)]
    if body.audios:
        payload["audios"] = [_check_media(x, f"audios[{i}]", "audio") for i, x in enumerate(body.audios)]
    return _start_video_job(payload, VIDEO_DIMS[body.aspect_ratio])


@app.get("/api/video/status/{key}")
def video_status(key: str):
    """查询视频任务状态。key 为 job_id（新）或 video_id（兼容旧卡片/脚本）。
    completed 时返回落盘文件；completed_download_failed 时附原始 URL 供兜底。"""
    job = _video_jobs.get(key)
    if job is None:
        # 兼容：传入的是 Agnes video_id —— 直接查一次上游；
        # 若也查不到（如服务重启后内存注册表已清空），明确告知而非报错
        try:
            resp = _agnes_video_status(key)
        except HTTPException:
            return {"ok": True, "status": "unknown",
                    "error": "任务记录不存在（服务可能重启过），请重新生成"}
        status = resp.get("status", "")
        return {"ok": True, "status": status, "progress": resp.get("progress", 0),
                "error": resp.get("error") or resp.get("message") or "",
                "source_url": resp.get("url") or ""}
    out = {
        "ok": True,
        "status": job["status"],
        "progress": job.get("progress", 0),
        "create_attempt": job.get("create_attempt", 0),
        "video_id": job.get("video_id", ""),
        "error": job.get("error", ""),
        "source_url": job.get("source_url", ""),
        "elapsed": job.get("elapsed", 0),
        "expected": job.get("expected", ""),
    }
    if job["status"] == "completed" and job.get("file"):
        out["file"] = job["file"]
        out["src"] = f"/api/files/{job['file']}"
        out["bytes"] = job.get("bytes", 0)
    return out


# ---------- 结果文件 ----------

@app.get("/api/files/{name}")
def get_file(name: str, download: int = 0):
    """读取/下载 outputs/ 中的结果文件；?download=1 时作为附件下载"""
    if Path(name).name != name:
        raise HTTPException(400, "非法文件名")
    path = OUTPUTS_DIR / name
    if not path.is_file():
        raise HTTPException(404, "文件不存在或已被清理")
    media = mimetypes.guess_type(name)[0] or "application/octet-stream"
    disposition = "attachment" if download else "inline"
    return FileResponse(
        path,
        media_type=media,
        headers={"Content-Disposition": f"{disposition}; filename*=UTF-8''{quote(name)}"},
    )


@app.get("/api/health")
def health():
    return {"ok": True, "key_configured": bool(AGNES_KEY)}


@app.get("/")
def index():
    return FileResponse(STATIC_DIR / "index.html")


@app.exception_handler(Exception)
async def unhandled_exception_handler(request, exc: Exception):
    """兜底：任何未捕获异常都打印完整堆栈（server.log），并返回可读的中文错误"""
    import traceback
    print("\n[未捕获异常] %s %s" % (request.method, request.url.path), flush=True)
    traceback.print_exc()
    print("[未捕获异常结束]\n", flush=True)
    return JSONResponse(
        status_code=500,
        content={"detail": f"服务内部错误：{exc!r}（详情见 server.log）"},
    )


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="127.0.0.1", port=8000, reload=True)
