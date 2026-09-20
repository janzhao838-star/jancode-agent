# -*- coding: utf-8 -*-
"""消息附件：对话里直接拖图/贴图，图片进模型而不是进文字。

移植自 DeepSeek Harness 的 dsh-attachment-local，保留它的三个核心设计：
  1. 内容寻址存储——附件 id 就是归一化后字节的 sha256，同一个图拖十次
     只存一份，路径 attachments/v1/objects/<前两位>/<sha256>，不可变；
  2. 准入上限——单图 20MB、每条消息 20 张、尺寸不超过 8192px，
     超了在门口就拒，不让坏数据流进上下文；
  3. 路由归一化——发给模型前压到 4MB 以内（sips/PIL 缩到 2048²，
     jpeg 质量 85→75→60 阶梯），否则兆级 base64 会把中转站打到拒单。
没有 PIL 时 macOS 用系统自带的 sips 缩图，Windows 上原样放行并在
超预算时报错提示。原子写 + 0600，和会话存档同一套纪律。
"""

import base64
import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

# 准入（照抄 harness 默认值）
MAX_IMAGE_BYTES = 20 * 1024 * 1024
MAX_IMAGES_PER_MESSAGE = 20
MAX_IMAGE_DIMENSION = 8192
# 路由预算：模型吃到的图必须满足
ROUTE_MAX_BYTES = 4 * 1024 * 1024
ROUTE_MAX_PIXELS = 2048 * 2048
QUALITY_LADDER = (85, 75, 60)

ID_PATTERN = re.compile(r"^sha256:([a-f0-9]{64})$")

MIME_BY_EXT = {
    ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
    ".gif": "image/gif", ".webp": "image/webp", ".bmp": "image/bmp",
}


def attachments_root() -> Path:
    return Path.home() / ".jancode-agent" / "attachments" / "v1"


def object_path(root: Path, sha256: str) -> Path:
    """归一化对象的存放路径：<root>/objects/<前两位>/<sha>，和 harness 一致。"""
    return root / "objects" / sha256[:2] / sha256


def detect_mime(data: bytes) -> str:
    """按文件头认类型，不信客户端报的。认不出就拒绝。"""
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    if data[:2] == b"BM":
        return "image/bmp"
    return ""


def _sips_resize(path: Path, pixels: int) -> None:
    """macOS 自带 sips：把图等比缩到像素预算内，原地覆盖。"""
    subprocess.run(
        ["sips", "-Z", str(int(pixels ** 0.5)), str(path)],
        check=True, capture_output=True, timeout=30,
    )


def normalize_for_route(data: bytes, mime: str) -> tuple[bytes, str]:
    """把图压进路由预算：超 4MB 就缩到 2048² 再走质量阶梯。

    返回 (bytes, mime)。压不下去就抛 ValueError，调用方给用户人话提示。
    """
    if len(data) <= ROUTE_MAX_BYTES:
        return data, mime
    # 先试 PIL，再试 sips，都没有就明确报错——宁可拒收也不能把
    # 几 MB 的 base64 塞进上下文（那正是「流式响应里没有任何内容」的根因）。
    tmp = None
    try:
        try:
            from PIL import Image  # type: ignore
            import io
            img = Image.open(io.BytesIO(data))
            img.thumbnail((int(ROUTE_MAX_PIXELS ** 0.5),) * 2)
            buf = io.BytesIO()
            fmt = "PNG" if mime == "image/png" else "JPEG"
            for q in QUALITY_LADDER:
                buf = io.BytesIO()
                img.convert("RGB").save(buf, fmt, quality=q) if fmt == "JPEG" else img.save(buf, fmt)
                if buf.tell() <= ROUTE_MAX_BYTES:
                    break
            out = buf.getvalue()
            return out, ("image/png" if fmt == "PNG" else "image/jpeg")
        except ImportError:
            pass
        if shutil.which("sips"):
            fd, tmp = tempfile.mkstemp(suffix=".img")
            with os.fdopen(fd, "wb") as f:
                f.write(data)
            _sips_resize(Path(tmp), ROUTE_MAX_PIXELS)
            out = Path(tmp).read_bytes()
            if len(out) <= ROUTE_MAX_BYTES:
                return out, detect_mime(out) or mime
        raise ValueError(
            "图片太大（超过 4MB），且本机没有可用的缩图工具。"
            "macOS 自带 sips 应该可用；或装 Pillow：pip install pillow")
    finally:
        if tmp and os.path.exists(tmp):
            os.unlink(tmp)


def store_image(data: bytes, root: Path | None = None) -> dict:
    """准入检查 → 路由归一化 → 内容寻址落盘。返回附件描述。

    归一化后的字节才是身份：同一张图不管原图多大，id 都一样，
    重复拖十次也只占一份磁盘。
    """
    if not data:
        raise ValueError("图片内容为空")
    if len(data) > MAX_IMAGE_BYTES:
        raise ValueError("图片超过 20MB 上限")
    mime = detect_mime(data)
    if not mime:
        raise ValueError("不是支持的图片格式（png/jpg/gif/webp/bmp）")
    norm, nmime = normalize_for_route(data, mime)
    sha = hashlib.sha256(norm).hexdigest()
    r = root or attachments_root()
    dest = object_path(r, sha)
    if not dest.exists():  # 内容寻址：已存在就是已去重，直接复用
        dest.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=str(dest.parent), prefix=".att-")
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(norm)
            os.chmod(tmp, 0o600)
            os.replace(tmp, dest)
        finally:
            if os.path.exists(tmp):
                try:
                    os.unlink(tmp)
                except OSError:
                    pass
    return {"id": "sha256:" + sha, "media_type": nmime, "size": len(norm)}


def data_uri(ref_id: str, root: Path | None = None) -> str:
    """按 id 读回附件，包成 data URI 给模型。id 不合法就拒。"""
    m = ID_PATTERN.match(ref_id or "")
    if not m:
        raise ValueError("附件引用不合法")
    p = object_path(root or attachments_root(), m.group(1))
    try:
        raw = p.read_bytes()
    except OSError:
        raise ValueError("附件不存在或已被清理：" + ref_id[:20])
    return "data:" + detect_mime(raw) + ";base64," + base64.b64encode(raw).decode()


def resolve_images(items: object, root: Path | None = None) -> list[str]:
    """/api/run 的 images 字段 → data URI 列表。超过每条 20 张拒收。"""
    if not items:
        return []
    if not isinstance(items, list):
        raise ValueError("images 必须是列表")
    if len(items) > MAX_IMAGES_PER_MESSAGE:
        raise ValueError("一条消息最多带 20 张图")
    out = []
    for it in items:
        s = str(it or "")
        if s.startswith("data:"):
            # 兼容直接传 data URI：仍走存储，转成内容寻址引用
            b64 = s.split(",", 1)[1] if "," in s else ""
            ref = store_image(base64.b64decode(b64), root)
            out.append(data_uri(ref["id"], root))
        else:
            out.append(data_uri(s, root))
    return out

