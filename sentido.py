"""Descobre o sentido fiscalizado de cada radar pela via de mao unica mais proxima.

Em avenida com canteiro, cada pista e mapeada no OpenStreetMap como uma via de mao unica. Um radar colado numa
dessas pistas so fiscaliza o sentido dela: o app usa isso para ignorar o radar da outra mao.

Entrada:
  radares.geojson  radares do OpenStreetMap (osmium export)
  cet.csv          base da CET (so os locais ativos contam)
  vias.geojsonseq  vias de carro do Brasil (osmium export -f geojsonseq), uma por linha
Saida:
  sentidos.json    {"<lat x 100000>,<lon x 100000>": graus}, so dos radares com sentido seguro

So grava sentido quando a via mais proxima e de mao unica, esta a ate DIST_MAX metros e nao ha outra via quase
tao perto (perto de cruzamento ou entre as duas pistas, fica sem sentido e o app avisa nos dois sentidos).

Uso: python3 sentido.py radares.geojson cet.csv vias.geojsonseq sentidos.json
"""
import csv
import json
import math
import sys

DIST_MAX = 20.0      # metros do radar ate o eixo da pista
FOLGA = 8.0          # a segunda via mais proxima precisa estar pelo menos FOLGA metros mais longe...
RAZAO = 2.0          # ...ou RAZAO vezes mais longe
CELULA = 0.001       # grade de busca, graus (~110 m)
M_POR_GRAU = 111195.0


def chave(lat, lon):
    # mesmo arredondamento do gerar_base.ps1 ([Math]::Floor(x * 100000 + 0.5))
    return f"{math.floor(lat * 100000 + 0.5)},{math.floor(lon * 100000 + 0.5)}"


def celula(lat, lon):
    return (math.floor(lat / CELULA), math.floor(lon / CELULA))


def carregar_radares(arq_osm, arq_cet):
    pts = {}
    with open(arq_osm, encoding="utf-8") as f:
        for ft in json.load(f)["features"]:
            g = ft.get("geometry") or {}
            if g.get("type") == "Point":
                lon, lat = g["coordinates"][:2]
                pts[chave(lat, lon)] = (lat, lon)
    with open(arq_cet, encoding="utf-8-sig") as f:
        leitor = csv.reader(f)
        next(leitor)
        for c in leitor:
            if len(c) < 7 or c[2].strip():   # coluna DESATIVACAO preenchida: local desativado
                continue
            try:
                lat, lon = float(c[5]), float(c[6])
            except ValueError:
                continue
            pts[chave(lat, lon)] = (lat, lon)
    return pts


def mao_unica(p):
    ow = (p.get("oneway") or "").lower()
    if ow in ("yes", "true", "1"):
        return 1
    if ow == "-1":
        return -1
    if ow == "no":
        return 0
    if p.get("highway") in ("motorway", "motorway_link") or p.get("junction") in ("roundabout", "circular"):
        return 1
    return 0


def dist_segmento(lat, lon, a, b):
    """Distancia em metros do ponto ao segmento a-b ([lon, lat]) e o rumo do segmento em graus (a -> b)."""
    cl = math.cos(math.radians(lat))
    ax, ay = (a[0] - lon) * M_POR_GRAU * cl, (a[1] - lat) * M_POR_GRAU
    bx, by = (b[0] - lon) * M_POR_GRAU * cl, (b[1] - lat) * M_POR_GRAU
    dx, dy = bx - ax, by - ay
    l2 = dx * dx + dy * dy
    t = 0.0 if l2 == 0 else max(0.0, min(1.0, -(ax * dx + ay * dy) / l2))
    px, py = ax + t * dx, ay + t * dy
    return math.hypot(px, py), math.degrees(math.atan2(dx, dy)) % 360


def main(arq_osm, arq_cet, arq_vias, arq_saida):
    radares = carregar_radares(arq_osm, arq_cet)
    grade = {}
    for k, (lat, lon) in radares.items():
        ci, cj = celula(lat, lon)
        for di in (-1, 0, 1):
            for dj in (-1, 0, 1):
                grade.setdefault((ci + di, cj + dj), []).append(k)

    melhor = {}   # chave do radar -> lista das 2 vias mais proximas [(dist, id_via, rumo, mao)]
    vias = 0
    with open(arq_vias, encoding="utf-8") as f:
        for linha in f:
            linha = linha.strip().lstrip("\x1e")
            if not linha:
                continue
            ft = json.loads(linha)
            g = ft.get("geometry") or {}
            if g.get("type") != "LineString":
                continue
            vias += 1
            coords = g["coordinates"]
            # radares perto de algum ponto da via (pontos intermediarios a cada ~100 m nos trechos longos)
            perto = set()
            for i in range(len(coords)):
                lon, lat = coords[i][:2]
                perto.update(grade.get(celula(lat, lon), ()))
                if i + 1 < len(coords):
                    lon2, lat2 = coords[i + 1][:2]
                    passos = int(max(abs(lat2 - lat), abs(lon2 - lon)) / CELULA)
                    for s in range(1, passos + 1):
                        f2 = s / (passos + 1)
                        perto.update(grade.get(celula(lat + (lat2 - lat) * f2, lon + (lon2 - lon) * f2), ()))
            if not perto:
                continue
            p = ft.get("properties") or {}
            mao = mao_unica(p)
            id_via = ft.get("id") or p.get("@id") or id(ft)
            for k in perto:
                lat, lon = radares[k]
                dmin, rumo = None, 0.0
                for i in range(len(coords) - 1):
                    d, r = dist_segmento(lat, lon, coords[i], coords[i + 1])
                    if dmin is None or d < dmin:
                        dmin, rumo = d, r
                if dmin is None or dmin > 60:
                    continue
                lista = melhor.setdefault(k, [])
                lista.append((dmin, id_via, rumo, mao))
                lista.sort(key=lambda x: x[0])
                del lista[2:]

    sentidos = {}
    for k, lista in melhor.items():
        d1, _, rumo, mao = lista[0]
        if mao == 0 or d1 > DIST_MAX:
            continue
        if len(lista) > 1:
            d2, _, rumo2, mao2 = lista[1]
            sentido2 = (rumo2 + (180 if mao2 < 0 else 0)) % 360
            mesma_pista = mao2 != 0 and abs((sentido2 - (rumo + (180 if mao < 0 else 0)) + 180) % 360 - 180) < 30
            # outra via quase tao perto, que nao seja a continuacao da mesma pista: sentido incerto
            if not mesma_pista and d2 - d1 < FOLGA and d2 < d1 * RAZAO:
                continue
        sentidos[k] = round((rumo + (180 if mao < 0 else 0)) % 360)

    with open(arq_saida, "w", encoding="ascii") as f:
        json.dump(sentidos, f, separators=(",", ":"))
    print(f"Vias lidas: {vias}. Radares: {len(radares)}, com via perto: {len(melhor)}, com sentido seguro: {len(sentidos)}.")


if __name__ == "__main__":
    main(*sys.argv[1:5])
