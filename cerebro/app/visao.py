"""Imagem: movimento dentro da caixa do cão e recortes para o Claude."""
import io

import numpy as np
from PIL import Image, ImageFilter


def abrir(jpg: bytes) -> Image.Image:
    return Image.open(io.BytesIO(jpg)).convert("RGB")


def _area(img: Image.Image, caixa, margem: float):
    x1, y1, x2, y2 = caixa
    w, h = x2 - x1, y2 - y1
    return (max(0, int(x1 - w * margem)), max(0, int(y1 - h * margem)),
            min(img.width, int(x2 + w * margem)), min(img.height, int(y2 + h * margem)))


def movimento(antes: Image.Image, depois: Image.Image, caixa, limiar: int = 25) -> float:
    """Fração (0-1) dos píxeis da caixa que mudaram entre duas imagens."""
    area = _area(depois, caixa, 0.0)
    if area[2] - area[0] < 4 or area[3] - area[1] < 4:
        return 0.0
    a, b = (np.asarray(i.crop(area).convert("L").filter(ImageFilter.GaussianBlur(2)), dtype=np.int16)
            for i in (antes, depois))
    return float((np.abs(a - b) > limiar).mean())


def recorte(img: Image.Image, caixa, margem: float = 0.3, lado_minimo: int = 512) -> bytes:
    """Recorte do cão com margem, ampliado até o lado menor ter pelo menos `lado_minimo` px."""
    r = img.crop(_area(img, caixa, margem))
    escala = lado_minimo / min(r.width, r.height)
    if escala > 1:
        r = r.resize((round(r.width * escala), round(r.height * escala)), Image.LANCZOS)
    out = io.BytesIO()
    r.save(out, "JPEG", quality=85)
    return out.getvalue()
