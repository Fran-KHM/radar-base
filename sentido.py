"""Descobre o sentido fiscalizado de cada radar pela via de mao unica mais proxima.

Em avenida com canteiro, cada pista e mapeada no OpenStreetMap como uma via de mao unica. Um radar colado numa
dessas pistas so fiscaliza o sentido dela: o app usa isso para ignorar o radar da outra mao.

Entrada:
  radares.geojson  radares do OpenStreetMap (osmium export)
  cet.csv          base da CET (so os locais ativos contam)
  vias.geojsonseq  vias de carro do Brasil (osmium export -f geojsonseq), uma por linha
Saida:
  sentidos.json    {"<lat x 100000>,<lon x 100000>": graus}, so dos radares com sentido seguro
  pistas.json      (opcional) {"<chave>": [dlat, dlon, ...]} tracado da pista +-400 m em volta do radar, em
                   graus x 100000 relativos ao radar; tambem para ruas de mao dupla (sem sentido)

So grava sentido quando a via mais proxima e de mao unica, esta a ate DIST_MAX metros e nao ha outra via quase
tao perto (perto de cruzamento ou entre as duas pistas, fica sem sentido e o app avisa nos dois sentidos).

Uso: python3 sentido.py radares.geojson cet.csv vias.geojsonseq sentidos.json [pistas.json [chegadas.json]]
"""
import csv
import heapq
import json
import math
import re
import sys
import unicodedata

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


# ---------------------------------------------------------------------------------------------------------------
# Sentido declarado pela CET (desde 06/11/2026). Como no Waze, o sentido fiscalizado e um dado do radar, nao uma
# conclusao tirada da posicao do ponto: a descricao da CET traz "(CENTRO/BAIRRO)", "B/C", "(INTERLAGOS/C BRANCO)"...
# O ponto so serve para achar a rua (pelo nome) e o rumo dela; o sentido vem do texto. Pela regra de numeracao de
# Sao Paulo, a rua comeca na ponta mais perto do Marco Zero: CENTRO/BAIRRO = afastando-se do comeco da rua.
# ---------------------------------------------------------------------------------------------------------------
MARCO_ZERO = (-23.55031, -46.63395)
DIST_NOME = 40.0       # metros: via com o nome da rua a ate esta distancia do ponto
PASSO = 30.0           # metros andados em cada sentido para comparar
DECISAO_MIN = 10.0     # metros de diferenca minima entre os dois sentidos para decidir
RAIO_RUA = 12000.0     # metros: trechos da mesma rua considerados para achar o comeco dela

IGNORAR = set("""AV AVENIDA R RUA AL ALAMEDA EST ESTR ESTRADA PCA PRACA PC VD VIAD VIADUTO PTE PONTE TV TRAV TRAVESSA
LG LARGO ROD RODOVIA MARG DR DRA DEP DEPUTADO PROF PROFA PROFESSOR PROFESSORA ENG ENGENHEIRO GAL GEN GENERAL CEL
CORONEL PRES PRESIDENTE SEN SENADOR DAS DOS VER VEREADOR BRIG BRIGADEIRO MAL MARECHAL CAP CAPITAO DONA STA SANTA STO
SANTO SAO NOSSA SENHORA SRA COMEND COMENDADOR CONS CONSELHEIRO GOV GOVERNADOR PREF PREFEITO ALM ALMIRANTE TEN
TENENTE MAJ MAJOR ARQ ARQUITETO JORN JORNALISTA MIN MINISTRO DOUTOR DOUTORA VISC VISCONDE BARAO CONDE MARQUES DUQUE
JARDIM VILA NUMERO""".split())

_MARG_PINHEIROS = [(-23.533, -46.737), (-23.567, -46.705), (-23.600, -46.695), (-23.640, -46.722), (-23.680, -46.705)]
_MARG_TIETE = [(-23.520, -46.730), (-23.517, -46.680), (-23.515, -46.630), (-23.520, -46.580), (-23.510, -46.540)]
LUGARES = {
    "CENTRO": [MARCO_ZERO],
    "CASTELOBRANCO": [(-23.5235, -46.7440)], "CBRANCO": [(-23.5235, -46.7440)], "CB": [(-23.5235, -46.7440)],
    "AYRTONSENNA": [(-23.5160, -46.5575)], "ASENNA": [(-23.5160, -46.5575)], "AS": [(-23.5160, -46.5575)],
    "INTERLAGOS": [(-23.7010, -46.6980)],
    "IMIGRANTES": [(-23.6130, -46.6380)], "IMIGRAN": [(-23.6130, -46.6380)], "IMIG": [(-23.6130, -46.6380)],
    "ANCHIETA": [(-23.6060, -46.6020)],
    "MARGINAL": _MARG_PINHEIROS + _MARG_TIETE, "MARG": _MARG_PINHEIROS + _MARG_TIETE,
    "MPINHEIROS": _MARG_PINHEIROS, "MARGINALPINHEIROS": _MARG_PINHEIROS, "MARGPINHEIROS": _MARG_PINHEIROS,
    "MTIETE": _MARG_TIETE, "MARGINALTIETE": _MARG_TIETE, "MARGTIETE": _MARG_TIETE,
    "DIADEMA": [(-23.6860, -46.6230)],
    "SANTANA": [(-23.5020, -46.6250)],
    "AEROPORTO": [(-23.6270, -46.6560)],
    "IPIRANGA": [(-23.5880, -46.6080)],
    "ITAIM": [(-23.5850, -46.6760)],
    "PINHEIROS": [(-23.5670, -46.6930)],
    "MAUA": [(-23.6680, -46.4610)],
    "CONSOLACAO": [(-23.5530, -46.6600)],
    "PARAISO": [(-23.5760, -46.6410)],
}

_CB = r"(?:CENTRO|C)\s*/\s*(?:BAIRRO|B)"
_BC = r"(?:BAIRRO|B)\s*/\s*(?:CENTRO|C)"


def normalizar(s):
    s = unicodedata.normalize("NFKD", (s or "").upper())
    return "".join(c for c in s if not unicodedata.combining(c))


def tokens(s):
    return frozenset(t for t in re.findall(r"[A-Z0-9]+", normalizar(s)) if len(t) >= 3 and t not in IGNORAR)


def ler_descricao(desc):
    """Da descricao da CET: lista das ruas (conjuntos de palavras; a primeira e a fiscalizada) e o sentido
    ("cb", "bc", "ambos", ("para", origem, destino) ou None)."""
    d = normalizar(desc)
    cb = re.search(r"(?<![A-Z])" + _CB + r"(?![A-Z])", d)
    bc = re.search(r"(?<![A-Z])" + _BC + r"(?![A-Z])", d)
    if cb and bc:
        alvo = "ambos"          # um registro para as duas maos (ex.: "R GUAIPA B/C, NR 489/R GUAIPA C/B, NR 486")
    elif cb:
        alvo = "cb"
    elif bc:
        alvo = "bc"
    else:
        m = re.search(r"\(([^()/]*?)\s*/\s*([^()/]*?)\)", d)
        alvo = ("para", m.group(1).strip(), m.group(2).strip()) if m else None
    cab = re.split(r"[(,]| - |\bA MAIS\b|\bOPOSTO\b|\bALTURA\b|\bNUMERO\b|\bNR\b", d, maxsplit=1)[0]
    cab = re.split(r"(?<![A-Z])(?:" + _CB + "|" + _BC + r")(?![A-Z])", cab, maxsplit=1)[0]
    ruas = [t for t in (tokens(x) for x in re.split(r"\bX\b", cab)) if t]
    return ruas, alvo


def lugar(nome):
    return LUGARES.get(re.sub(r"[^A-Z]", "", normalizar(nome)))


def metros(lat, lon, lat2, lon2):
    cl = math.cos(math.radians(lat))
    return math.hypot((lon2 - lon) * M_POR_GRAU * cl, (lat2 - lat) * M_POR_GRAU)


def andar(lat, lon, rumo, m):
    r = math.radians(rumo)
    return lat + m * math.cos(r) / M_POR_GRAU, lon + m * math.sin(r) / (M_POR_GRAU * math.cos(math.radians(lat)))


def dist_lista(lat, lon, pts):
    return min(metros(lat, lon, a, b) for a, b in pts)


def escolher_sentido(lat, lon, rumo_via, medida, quer_aumentar):
    """Entre rumo_via e rumo_via + 180, o que faz `medida` (funcao de lat, lon) aumentar (ou diminuir).
    Devolve (rumo, folga em metros) ou (None, folga) quando a diferenca e pequena demais para decidir."""
    base = medida(lat, lon)
    v = []
    for h in (rumo_via % 360, (rumo_via + 180) % 360):
        la, lo = andar(lat, lon, h, PASSO)
        v.append((medida(la, lo) - base, h))
    v.sort(reverse=quer_aumentar)
    folga = abs(v[0][0] - v[1][0])
    return (v[0][1] if folga >= DECISAO_MIN else None), folga


def casa_nome(rua, nome_via):
    """A via tem todas as palavras da rua da CET (ou a CET tem todas as da via)."""
    return bool(rua) and bool(nome_via) and (rua <= nome_via or nome_via <= rua)


def difang(a, b):
    return abs((a - b + 180) % 360 - 180)


def sentido_via(c):
    """Sentido de circulacao de uma via candidata (dist, rumo, mao, ...) de mao unica."""
    return (c[2] + (180 if c[3] < 0 else 0)) % 360


def comeco_da_rua(lat, lon, rua, trechos, por_palavra):
    """Ponto da rua (trechos com o mesmo nome a ate RAIO_RUA) mais perto do Marco Zero: onde a numeracao comeca."""
    rara = min(rua, key=lambda t: len(por_palavra.get(t, ())))
    inicio, dmz = None, None
    for i in por_palavra.get(rara, ()):
        nome_t, cs = trechos[i]
        if not casa_nome(rua, nome_t):
            continue
        for c in cs:
            lo, la = c[0], c[1]
            if metros(lat, lon, la, lo) > RAIO_RUA:
                continue
            dm = metros(MARCO_ZERO[0], MARCO_ZERO[1], la, lo)
            if dmz is None or dm < dmz:
                inicio, dmz = (la, lo), dm
    return inicio


def sentido_declarado(pt, ruas, alvo, perto, trechos, por_palavra):
    """Sentido (graus) do radar da CET pelo texto da descricao e as coordenadas da pista certa para o tracado.
    Devolve (sentido ou None, coords da pista ou None, status)."""
    lat, lon = pt
    if alvo is None or not ruas:
        return None, None, "sem texto"
    # vias a ate DIST_NOME com o nome de alguma das ruas (a primeira e a fiscalizada; as outras, cruzamentos)
    cands = [(i, d, rumo, mao, coords, rua) for i, rua in enumerate(ruas) for d, rumo, mao, coords, nome in perto
             if casa_nome(rua, nome)]
    if not cands:
        return None, None, "rua nao achada"
    # (i, d, rumo, mao, coords, rua) -> as funcoes abaixo usam c[1:] no formato (dist, rumo, mao, coords)
    if alvo == "ambos":
        primeira = min(c[0] for c in cands)
        duplas = sorted((c for c in cands if c[0] == primeira and c[3] == 0), key=lambda c: c[1])
        return None, (duplas[0][4] if duplas else None), "ambos"

    if alvo in ("cb", "bc"):
        primeira = min(c[0] for c in cands)
        c0 = min((c for c in cands if c[0] == primeira), key=lambda c: c[1])
        inicio = comeco_da_rua(lat, lon, c0[5], trechos, por_palavra)
        h = None
        if inicio is not None:
            h, _ = escolher_sentido(lat, lon, c0[2], lambda a, b: metros(inicio[0], inicio[1], a, b), alvo == "cb")
        if h is None:
            # comeco da rua indefinido ou de lado: aproxima pelo Marco Zero
            h, _ = escolher_sentido(lat, lon, c0[2], lambda a, b: metros(MARCO_ZERO[0], MARCO_ZERO[1], a, b), alvo == "cb")
        escolhida = c0
    else:
        origem, destino = lugar(alvo[1]), lugar(alvo[2])
        if not origem and not destino:
            return None, None, "lugar desconhecido"

        def medida(a, b):   # menor = mais perto do destino e mais longe da origem
            v = 0.0
            if destino:
                v += dist_lista(a, b, destino)
            if origem:
                v -= dist_lista(a, b, origem)
            return v
        h, escolhida, melhor_folga = None, None, -1.0
        for c in cands:   # rua fiscalizada e cruzamentos: fica a que decide com mais folga
            hc, folga = escolher_sentido(lat, lon, c[2], medida, False)
            if hc is not None and folga > melhor_folga:
                h, escolhida, melhor_folga = hc, c, folga
    if h is None:
        return None, None, "indeciso"

    da_rua = [c for c in cands if c[5] == escolhida[5]]
    unicas = [c for c in da_rua if c[3] != 0]
    status = "ok"
    # rua de mao unica (todas as vias dela aqui no mesmo sentido): o transito so anda para um lado
    if unicas and len(unicas) == len(da_rua) and all(difang(sentido_via(c), sentido_via(unicas[0])) < 60 for c in unicas):
        if difang(sentido_via(unicas[0]), h) > 90:
            status = "mao unica corrigiu"
        h = sentido_via(unicas[0])
    certa = [c for c in unicas if difang(sentido_via(c), h) < 60]
    dupla = [c for c in da_rua if c[3] == 0]
    pista = min(certa, key=lambda c: c[1])[4] if certa else (min(dupla, key=lambda c: c[1])[4] if dupla else None)
    return h, pista, status


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
            cet_info[chave(lat, lon)] = (c[4],) + ler_descricao(c[4])
    return pts


cet_info = {}   # chave do radar da CET -> (descricao, ruas, alvo)


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


arq_pistas = None   # pistas.json (opcional, 5o argumento): tracado da pista de cada radar
arq_chegadas = None   # chegadas.json (opcional, 6o argumento): ruas que levam a cada radar


# ---------------------------------------------------------------------------------------------------------------
# Ruas de chegada (07/11/2026). O relogio so via a pista do radar (+-400 m de uma via do OSM, as vezes so a quadra
# do radar): radar logo depois de uma curva ou de uma esquina so aparecia em cima. Agora a base guarda tambem os
# caminhos de carro que levam ao radar nos ultimos ALCANCE_CHEGADA metros, respeitando a mao das vias, como uma
# arvore que sai do radar e anda para tras. Cada caminho vai na ordem em que o carro anda (da ponta para o radar):
# o app avisa quando estamos em cima de um deles e andando no sentido dele.
# ---------------------------------------------------------------------------------------------------------------
CEL_REDE = 0.003         # graus (~330 m): vias a ate ~1 celula dos radares entram na rede
ALCANCE_CHEGADA = 220.0  # metros de caminho para tras a partir do radar
MAX_PONTOS_CHEGADA = 30  # pontos por radar (o bloco vai para o relogio)
TOL_CHEGADA = 3.0        # metros: simplificacao dos caminhos


def _cels_segmento(u, v):
    cels = set()
    passos = int(max(abs(v[1] - u[1]), abs(v[0] - u[0])) / CELULA)
    for s in range(passos + 2):
        f = min(1.0, s / (passos + 1))
        cels.add(celula(u[1] + (v[1] - u[1]) * f, u[0] + (v[0] - u[0]) * f))
    return cels


def calcular_chegadas(radares, sentidos, rede):
    entra = {}    # no -> nos de onde se chega nele (respeitando a mao)
    segs = {}     # celula -> segmentos percorriveis (u -> v)
    for coords, mao in rede:
        for i in range(len(coords) - 1):
            a, b = tuple(coords[i][:2]), tuple(coords[i + 1][:2])
            if a == b:
                continue
            pares = [(a, b)] if mao == 1 else [(b, a)] if mao == -1 else [(a, b), (b, a)]
            for u, v in pares:
                entra.setdefault(v, set()).add(u)
                for c in _cels_segmento(u, v):
                    segs.setdefault(c, []).append((u, v))
    saida = {}
    for k, (lat, lon) in radares.items():
        h = sentidos.get(k)
        ci, cj = celula(lat, lon)
        cand, vistos = [], set()
        for di in (-1, 0, 1):
            for dj in (-1, 0, 1):
                for u, v in segs.get((ci + di, cj + dj), ()):
                    if (u, v) in vistos:
                        continue
                    vistos.add((u, v))
                    d, r = dist_segmento(lat, lon, u, v)
                    if d <= DIST_MAX + 10:
                        cand.append((d, r, u, v))
        if not cand:
            continue
        cand.sort(key=lambda c: c[0])
        if h is not None:
            inicios = [c for c in cand if difang(c[1], h) <= 60][:1]
        else:
            # sem sentido: o radar vale para os dois sentidos da via mais perto
            inicios = []
            for c in cand:
                if c[0] > cand[0][0] + 8:
                    break
                if all(difang(c[1], x[1]) > 90 for x in inicios):
                    inicios.append(c)
                if len(inicios) == 2:
                    break
        cadeias = []
        for _, _, u, v in inicios:
            cadeias += arvore_chegada(lat, lon, u, v, entra)
        saida_k, total = [], 0
        for cad in cadeias:
            if total + len(cad) // 2 > MAX_PONTOS_CHEGADA:
                break
            saida_k.append(cad)
            total += len(cad) // 2
        if saida_k:
            saida[k] = saida_k
    return saida


def arvore_chegada(lat, lon, u, v, entra):
    """Caminhos que chegam ao radar entrando pelo segmento u -> v. Cada caminho: [dlat, dlon, ...] x 100000
    relativos ao radar, na ordem em que o carro anda; o ultimo ponto de um deles e a projecao do radar."""
    cl = math.cos(math.radians(lat))
    ax, ay = (u[0] - lon) * M_POR_GRAU * cl, (u[1] - lat) * M_POR_GRAU
    bx, by = (v[0] - lon) * M_POR_GRAU * cl, (v[1] - lat) * M_POR_GRAU
    dx, dy = bx - ax, by - ay
    l2 = dx * dx + dy * dy
    t = 0.0 if l2 == 0 else max(0.0, min(1.0, -(ax * dx + ay * dy) / l2))
    proj = (lon + (ax + t * dx) / (M_POR_GRAU * cl), lat + (ay + t * dy) / M_POR_GRAU)
    d0 = metros(lat, lon, u[1], u[0]) if t > 0 else 0.0
    dist, pai, cortes = {u: d0}, {u: proj}, []
    fila = [(d0, u)]
    while fila:
        d, n = heapq.heappop(fila)
        if d > dist.get(n, 1e18):
            continue
        for p in entra.get(n, ()):
            if p == pai[n] or (n == u and p == v):
                continue   # meia-volta
            seg = metros(p[1], p[0], n[1], n[0])
            nd = d + seg
            if nd > ALCANCE_CHEGADA:
                f = (ALCANCE_CHEGADA - d) / seg if seg > 0 else 0.0
                if f > 0.05:
                    cortes.append(((n[0] + (p[0] - n[0]) * f, n[1] + (p[1] - n[1]) * f), n, ALCANCE_CHEGADA))
                continue
            if nd < dist.get(p, 1e18):
                dist[p] = nd
                pai[p] = n
                heapq.heappush(fila, (nd, p))
    filhos = {}
    for n in dist:
        if n != u:
            filhos[pai[n]] = filhos.get(pai[n], 0) + 1
    for _, n, _ in cortes:
        filhos[n] = filhos.get(n, 0) + 1
    folhas = [(dist[n], n, pai[n]) for n in dist if filhos.get(n, 0) == 0]
    folhas += [(dd, pt, n) for pt, n, dd in cortes]
    folhas.sort(key=lambda x: x[0])
    emitido, cadeias = set(), []
    for _, f, prox in folhas:
        pts, n = [f], prox
        while True:
            pts.append(n)
            if n == proj or n in emitido:
                break
            emitido.add(n)
            n = pai[n]
        if len(pts) < 2:
            continue
        xy = [((q[0] - lon) * M_POR_GRAU * cl, (q[1] - lat) * M_POR_GRAU) for q in pts]
        xy = simplificar(xy, TOL_CHEGADA)
        cad = []
        for x, y in xy:
            par = [round(y / M_POR_GRAU * 100000), round(x / (M_POR_GRAU * cl) * 100000)]
            if cad[-2:] != par:
                cad += par
        if len(cad) >= 4:
            cadeias.append(cad)
    return cadeias


def main(arq_osm, arq_cet, arq_vias, arq_saida):
    radares = carregar_radares(arq_osm, arq_cet)
    grade = {}
    for k, (lat, lon) in radares.items():
        ci, cj = celula(lat, lon)
        for di in (-1, 0, 1):
            for dj in (-1, 0, 1):
                grade.setdefault((ci + di, cj + dj), []).append(k)

    melhor = {}   # chave do radar -> lista das 2 vias mais proximas [(dist, id_via, rumo, mao)]
    perto_cet = {}   # chave do radar da CET -> vias a ate DIST_NOME [(dist, rumo, mao, coords, palavras do nome)]
    # trechos com nome na area da CET, por palavra do nome (para achar o comeco de cada rua)
    caixa = None
    if cet_info:
        las = [radares[k][0] for k in cet_info]
        los = [radares[k][1] for k in cet_info]
        caixa = (min(las) - 0.15, max(las) + 0.15, min(los) - 0.15, max(los) + 0.15)
    rede_cel = set()      # celulas de CEL_REDE em volta dos radares: vias dali entram na rede (ruas de chegada)
    if arq_chegadas:
        for lat, lon in radares.values():
            ci, cj = math.floor(lat / CEL_REDE), math.floor(lon / CEL_REDE)
            for di in (-1, 0, 1):
                for dj in (-1, 0, 1):
                    rede_cel.add((ci + di, cj + dj))
    rede = []             # [(coords, mao)] vias perto de algum radar
    trechos = []          # [(palavras, coords)]
    por_palavra = {}      # palavra -> [indice em trechos]
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
            p = ft.get("properties") or {}
            nome = None
            if caixa and caixa[0] <= coords[0][1] <= caixa[1] and caixa[2] <= coords[0][0] <= caixa[3] and p.get("name"):
                nome = tokens(p.get("name"))
                if nome:
                    trechos.append((nome, coords))
                    for t in nome:
                        por_palavra.setdefault(t, []).append(len(trechos) - 1)
            if rede_cel and any((math.floor(c[1] / CEL_REDE), math.floor(c[0] / CEL_REDE)) in rede_cel for c in coords):
                rede.append((coords, mao_unica(p)))
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
                if k in cet_info and dmin <= DIST_NOME:
                    perto_cet.setdefault(k, []).append((dmin, rumo, mao, coords, nome or tokens(p.get("name"))))
                lista = melhor.setdefault(k, [])
                lista.append((dmin, id_via, rumo, mao, coords))
                lista.sort(key=lambda x: x[0])
                del lista[2:]
            # viabilidade da opcao B (encaixe na pista no relogio): pontos de via de carro por bloco de 0,1 grau
            for lon, lat in (c[:2] for c in coords):
                kb = (math.floor((lat + 90) / 0.1), math.floor((lon + 180) / 0.1))
                pontos_bloco[kb] = pontos_bloco.get(kb, 0) + 1

    sentidos = {}
    pistas = {}
    for k, lista in melhor.items():
        d1, _, rumo, mao, coords = lista[0]
        if d1 > DIST_MAX:
            continue
        if len(lista) > 1:
            d2, _, rumo2, mao2, _ = lista[1]
            sentido2 = (rumo2 + (180 if mao2 < 0 else 0)) % 360
            mesma_pista = mao2 != 0 and abs((sentido2 - (rumo + (180 if mao < 0 else 0)) + 180) % 360 - 180) < 30
            # outra via quase tao perto, que nao seja a continuacao da mesma pista: pista e sentido incertos
            if not mesma_pista and d2 - d1 < FOLGA and d2 < d1 * RAZAO:
                continue
        # tracado da pista (ou da rua de mao dupla) +-ALCANCE_PISTA metros em volta do radar, para o app saber
        # se estamos nela ou numa pista paralela (Marginal: expressa, central e local)
        lat, lon = radares[k]
        pistas[k] = trecho_pista(coords, lat, lon)
        if mao != 0:
            sentidos[k] = round((rumo + (180 if mao < 0 else 0)) % 360)

    # Radares da CET: o sentido declarado na descricao manda; a posicao do ponto so acha a rua
    est = {}
    trocados = []
    for k, (desc, ruas, alvo) in cet_info.items():
        h, coords_pista, status = sentido_declarado(radares[k], ruas, alvo, perto_cet.get(k, []), trechos, por_palavra)
        est[status] = est.get(status, 0) + 1
        if status == "ambos":
            sentidos.pop(k, None)
            if coords_pista:
                pistas[k] = trecho_pista(coords_pista, *radares[k])
            else:
                pistas.pop(k, None)
            continue
        if h is None:
            continue
        antigo = sentidos.get(k)
        if antigo is not None and difang(antigo, h) > 90:
            trocados.append((desc, antigo, round(h)))
        sentidos[k] = round(h) % 360
        if coords_pista:
            pistas[k] = trecho_pista(coords_pista, *radares[k])
        else:
            pistas.pop(k, None)   # nao achou a pista certa: sem tracado o app nao descarta por pista
    if cet_info:
        print(f"CET: {len(cet_info)} radares ativos; sentido pela descricao: " +
              ", ".join(f"{s} {n}" for s, n in sorted(est.items(), key=lambda x: -x[1])))
        print(f"  sentido antigo (pista mais perto) contrario ao declarado: {len(trocados)}")
        for desc, a, n in trocados[:15]:
            print(f"    {a:>3} -> {n:>3}  {desc[:90]}")
        sem_lugar = {}
        for desc, ruas, alvo in cet_info.values():
            if isinstance(alvo, tuple) and not lugar(alvo[1]) and not lugar(alvo[2]):
                nome = f"{alvo[1]}/{alvo[2]}"
                sem_lugar[nome] = sem_lugar.get(nome, 0) + 1
        if sem_lugar:
            print("  lugares sem coordenada (entram no LUGARES): " +
                  "; ".join(f"{n} ({c})" for n, c in sorted(sem_lugar.items(), key=lambda x: -x[1])[:20]))

    with open(arq_saida, "w", encoding="ascii") as f:
        json.dump(sentidos, f, separators=(",", ":"))
    if arq_chegadas:
        chegadas = calcular_chegadas(radares, sentidos, rede)
        with open(arq_chegadas, "w", encoding="ascii") as f:
            json.dump(chegadas, f, separators=(",", ":"))
        n = sum(len(c) for v in chegadas.values() for c in v)
        print(f"Ruas de chegada: {len(chegadas)} radares, {n // 2} pontos (media {n / 2 / max(1, len(chegadas)):.1f}); "
              f"vias na rede: {len(rede)}.")
    if arq_pistas:
        with open(arq_pistas, "w", encoding="ascii") as f:
            json.dump(pistas, f, separators=(",", ":"))
    print(f"Vias lidas: {vias}. Radares: {len(radares)}, com via perto: {len(melhor)}, com sentido seguro: {len(sentidos)}, "
          f"com tracado da pista: {len(pistas)} (media {sum(len(v) for v in pistas.values()) / 2 / max(1, len(pistas)):.1f} pontos).")
    relatorio_b(pontos_bloco)


ALCANCE_PISTA = 400.0   # metros de pista guardados antes e depois do radar
TOL_SIMPLIFICA = 2.0    # metros: pontos que desviam menos que isso da reta saem


def trecho_pista(coords, lat, lon):
    """Pedaco da via em volta do radar, simplificado, em [dlat, dlon, ...] x 100000 relativos ao radar."""
    cl = math.cos(math.radians(lat))
    xy = [((c[0] - lon) * M_POR_GRAU * cl, (c[1] - lat) * M_POR_GRAU) for c in coords]
    # segmento mais perto do radar e o ponto de projecao nele
    melhor_i, melhor_d, proj = 0, None, (0.0, 0.0)
    for i in range(len(xy) - 1):
        (ax, ay), (bx, by) = xy[i], xy[i + 1]
        dx, dy = bx - ax, by - ay
        l2 = dx * dx + dy * dy
        t = 0.0 if l2 == 0 else max(0.0, min(1.0, -(ax * dx + ay * dy) / l2))
        px, py = ax + t * dx, ay + t * dy
        d = math.hypot(px, py)
        if melhor_d is None or d < melhor_d:
            melhor_i, melhor_d, proj = i, d, (px, py)
    # anda ALCANCE_PISTA para tras e para a frente a partir da projecao
    tras, falta, ant = [], ALCANCE_PISTA, proj
    for i in range(melhor_i, -1, -1):
        d = math.dist(ant, xy[i])
        if d >= falta:
            f = falta / d
            tras.append((ant[0] + (xy[i][0] - ant[0]) * f, ant[1] + (xy[i][1] - ant[1]) * f))
            break
        tras.append(xy[i]); falta -= d; ant = xy[i]
    frente, falta, ant = [], ALCANCE_PISTA, proj
    for i in range(melhor_i + 1, len(xy)):
        d = math.dist(ant, xy[i])
        if d >= falta:
            f = falta / d
            frente.append((ant[0] + (xy[i][0] - ant[0]) * f, ant[1] + (xy[i][1] - ant[1]) * f))
            break
        frente.append(xy[i]); falta -= d; ant = xy[i]
    linha = list(reversed(tras)) + [proj] + frente
    linha = simplificar(linha, TOL_SIMPLIFICA)
    saida = []
    for x, y in linha:
        saida += [round(y / M_POR_GRAU * 100000), round(x / (M_POR_GRAU * cl) * 100000)]
    return saida


def simplificar(pts, tol):
    """Douglas-Peucker iterativo."""
    if len(pts) < 3:
        return pts
    manter = [False] * len(pts)
    manter[0] = manter[-1] = True
    pilha = [(0, len(pts) - 1)]
    while pilha:
        a, b = pilha.pop()
        (ax, ay), (bx, by) = pts[a], pts[b]
        dx, dy = bx - ax, by - ay
        l = math.hypot(dx, dy) or 1e-9
        idx, dmax = None, tol
        for i in range(a + 1, b):
            d = abs((pts[i][0] - ax) * dy - (pts[i][1] - ay) * dx) / l
            if d > dmax:
                idx, dmax = i, d
        if idx is not None:
            manter[idx] = True
            pilha += [(a, idx), (idx, b)]
    return [p for p, m in zip(pts, manter) if m]


def relatorio_b(pontos_bloco):
    """Quanto pesaria mandar todas as vias de carro para o relogio (opcao B), nos blocos mais densos."""
    if not pontos_bloco:
        return
    def viz(kb):
        return sum(pontos_bloco.get((kb[0] + a, kb[1] + b), 0) for a in (-1, 0, 1) for b in (-1, 0, 1))
    top = sorted(pontos_bloco.items(), key=lambda x: -x[1])[:5]
    vals = sorted(pontos_bloco.values())
    print("Viabilidade B: pontos de via de carro por bloco de 0,1 grau (sem simplificar)")
    print(f"  blocos: {len(vals)}, mediana {vals[len(vals) // 2]}, maximo {vals[-1]}")
    for kb, n in top:
        print(f"  bloco {kb[0] * 10000 + kb[1]}: {n} pontos; com os 8 vizinhos: {viz(kb)}")


pontos_bloco = {}


if __name__ == "__main__":
    arq_pistas = sys.argv[5] if len(sys.argv) > 5 else None
    arq_chegadas = sys.argv[6] if len(sys.argv) > 6 else None
    main(*sys.argv[1:5])
