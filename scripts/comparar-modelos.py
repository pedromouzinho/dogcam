# Uso (repetir o teste das situações com um clip novo, sem mexer no Frigate):
#   1. Gravar um clip:   docker exec frigate /usr/lib/ffmpeg/7.0/bin/ffmpeg -rtsp_transport tcp \
#        -use_wallclock_as_timestamps 1 -i rtsp://127.0.0.1:8554/quarto_ipad -t 120 -c:v copy -an /tmp/clip.mp4
#   2. Tirar imagens a 2/s para /tmp/bench/frames/<situação>/ (uma pasta por situação, com ffmpeg -ss/-t -vf fps=2)
#   3. Copiar modelos .onnx a comparar para /tmp/bench/ (ex.: /config/model_cache/yolov9-s-320.onnx)
#   4. docker cp scripts/comparar-modelos.py frigate:/tmp/bench/ && docker exec frigate python3 /tmp/bench/comparar-modelos.py
"""Compara a deteção de "dog" entre o modelo atual do Frigate e o YOLOv9.

Corre dentro do contentor do Frigate (mesmo OpenVINO e mesma GPU), num processo
à parte, sem tocar na configuração. Para cada situação do clip, cada imagem é
dividida nos mesmos recortes quadrados (como as "regions" do Frigate) e fica o
score de dog mais alto.
"""
import glob, os, resource, sys, time
import cv2, numpy as np, openvino as ov

BASE = "/tmp/bench"
core = ov.Core()


def crops(img):
    h, w = img.shape[:2]
    for x in (0, 280, 560):                      # 3 quadrados grandes (720 px)
        yield img[0:720, x:x + 720]
    for y in (0, 160, 320):                      # 15 quadrados médios (400 px)
        for x in (0, 220, 440, 660, 880):
            yield img[y:y + 400, x:x + 400]


class SSD:
    nome = "SSDLite MobileNet v2 (atual)"

    def __init__(self):
        self.m = core.compile_model(core.read_model("/openvino-model/ssdlite_mobilenet_v2.xml"), "GPU")
        labels = [l.strip() for l in open("/openvino-model/coco_91cl_bkgr.txt")]
        self.dog = labels.index("dog")

    def prep(self, c):  # nhwc, bgr, uint8, 300x300 (como no config.yml)
        return cv2.resize(c, (300, 300))[np.newaxis]

    def dog_score(self, out):
        d = out[0][0][0]
        s = d[d[:, 1] == self.dog][:, 2]
        return float(s.max()) if len(s) else 0.0


class YOLO:
    def __init__(self, path, nome):
        self.nome = nome
        self.m = core.compile_model(core.read_model(path), "GPU")
        self.size = self.m.inputs[0].shape[2]

    def prep(self, c):  # nchw, rgb, float 0-1 (como o yolo-generic do Frigate)
        c = cv2.cvtColor(cv2.resize(c, (self.size, self.size)), cv2.COLOR_BGR2RGB)
        return (c.transpose(2, 0, 1)[np.newaxis].astype(np.float32) / 255.0)

    def dog_score(self, out):
        o = out[0][0]                            # (84, N): 4 caixa + 80 classes
        return float(o[4 + 16].max())            # COCO-80: dog = 16


modelos = [SSD()] + [YOLO(p, os.path.basename(p)[:-5]) for p in sorted(glob.glob(f"{BASE}/*.onnx"))]
situacoes = sorted(os.listdir(f"{BASE}/frames"))
imagens = {s: [cv2.imread(f) for f in sorted(glob.glob(f"{BASE}/frames/{s}/*.jpg"))] for s in situacoes}

for m in modelos:
    for c in list(crops(imagens[situacoes[0]][0]))[:5]:   # aquecer a GPU
        m.m(m.prep(c))
    print(f"\n== {m.nome}")
    tempos, r0, t0 = [], resource.getrusage(resource.RUSAGE_SELF), time.time()
    for s in situacoes:
        por_imagem = []
        for img in imagens[s]:
            melhor = 0.0
            for c in crops(img):
                x = m.prep(c)
                t = time.perf_counter()
                out = m.m(x)
                tempos.append(time.perf_counter() - t)
                melhor = max(melhor, m.dog_score(out))
            por_imagem.append(melhor)
        p = np.array(por_imagem)
        print(f"  {s:20s} máx {p.max():.2f}  mediana {np.median(p):.2f}  "
              f"imagens >=0.70: {(p >= 0.7).sum()}/{len(p)}  >=0.50: {(p >= 0.5).sum()}/{len(p)}")
    r1, t1 = resource.getrusage(resource.RUSAGE_SELF), time.time()
    cpu = (r1.ru_utime + r1.ru_stime - r0.ru_utime - r0.ru_stime) / (t1 - t0) * 100
    print(f"  tempo por recorte na GPU: média {np.mean(tempos)*1000:.1f} ms, p95 {np.percentile(tempos, 95)*1000:.1f} ms; "
          f"CPU do processo de teste: {cpu:.0f}% de um núcleo")
