# -*- coding: utf-8 -*-
"""
Agnes 生成工作台 — FastAPI 后端

职责：
  1. 持有 API Key（.env），代理 Agnes 图像接口（文生图 / 图生图 / 多图合成）
  2. 结果 URL 秒级失效，故生成成功后立即下载落盘到 outputs/
  3. 托管 static/ 单页前端

启动：python main.py  （默认 http://127.0.0.1:8000）
"""
import json
import mimetypes
import os
import time
import urllib.error
import urllib.request
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
MEDIA_HOSTS = ("platform-outputs.agnes-ai.space", "storage.googleapis.com", "apihub.agnes-ai.com")
MEDIA_TIMEOUT = 120
GEN_TIMEOUT = 300

OUTPUTS_DIR.mkdir(exist_ok=True)

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
            # 队列拥堵：等待后重试
            if e.code == 503 and "queue" in last_detail.lower() and attempt < len(retry_waits):
                wait = retry_waits[attempt]
                print(f"[ Agnes 队列拥堵 ] 第 {attempt + 1} 次重试，等待 {wait}s ...", flush=True)
                time.sleep(wait)
                continue
            if e.code == 503:
                raise HTTPException(503, "Agnes 服务繁忙（队列已满），已自动重试 "
                                        f"{attempt} 次仍失败，请稍后再试")
            raise HTTPException(502, f"Agnes 接口返回 HTTP {e.code}：{last_detail[:400]}")
        except Exception as e:
            raise HTTPException(502, f"Agnes 接口调用失败：{e!r}")
    raise HTTPException(503, "Agnes 服务持续拥堵，请稍后再试")


def _download(url: str) -> tuple[bytes, str]:
    """下载结果图片，返回 (字节, content-type)"""
    if not url.startswith("https://"):
        raise HTTPException(502, f"结果 URL 非 HTTPS，拒绝下载：{url[:80]}")
    if not any(host in url for host in MEDIA_HOSTS):
        raise HTTPException(502, f"结果 URL 不在信任域名白名单：{url[:80]}")
    req = urllib.request.Request(url, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=MEDIA_TIMEOUT) as resp:
            return resp.read(), resp.headers.get("Content-Type", "image/png")
    except Exception as e:
        raise HTTPException(502, f"结果下载失败（URL 可能已过期）：{e!r}")


def _save(data: bytes, content_type: str, prefix: str) -> str:
    """落盘 outputs/，返回文件名"""
    ext = ".jpg" if "jpeg" in content_type or "jpg" in content_type else ".png"
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
